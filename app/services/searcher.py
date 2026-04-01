"""
Searcher Service - Searches Pexels API for stock footage
Handles API rate limiting, pagination, and result aggregation
"""

import asyncio
from typing import List, Optional, Dict, Any
from datetime import datetime
import httpx

from app.config import get_settings
from app.models.schemas import StockClip, SearchResult, VisualCue
from app.utils.cost_tracker import get_cost_tracker


class SearcherService:
    """Searches stock footage APIs based on visual cues."""
    
    def __init__(self):
        cfg = get_settings()
        self.pexels_api_key = getattr(cfg, "pexels_api_key", "")
        self.pexels_base_url = "https://api.pexels.com/videos/search"
        self.cost_tracker = get_cost_tracker()
        self.rate_limit_delay = 0.5
        self._last_request_time = 0
    
    async def search_for_cue(
        self,
        cue: VisualCue,
        max_results_per_query: int = 5,
        job_id: Optional[str] = None
    ) -> SearchResult:
        """
        Search for stock footage matching a visual cue.
        
        Args:
            cue: The visual cue to search for
            max_results_per_query: Maximum results to return per search query
            job_id: Optional job ID for tracking
            
        Returns:
            SearchResult with aggregated clips
        """
        if not self.pexels_api_key:
            raise ValueError("Pexels API key not configured")
        
        all_clips = []
        queries_executed = []
        
        for query in cue.search_queries:
            await self._rate_limit()
            
            try:
                clips = await self._search_pexels(
                    query=query,
                    per_page=max_results_per_query,
                    orientation="landscape"  # Most b-roll is landscape
                )
                
                # Tag clips with source query and cue info
                for clip in clips:
                    clip.source_query = query
                    clip.cue_id = cue.id
                    clip.mood_match = cue.mood
                
                all_clips.extend(clips)
                queries_executed.append(query)
                
            except Exception as e:
                print(f"Search failed for query '{query}': {str(e)}")
                continue
        
        # Deduplicate by video ID
        unique_clips = self._deduplicate_clips(all_clips)
        
        return SearchResult(
            cue_id=cue.id,
            clips=unique_clips,
            total_results=len(unique_clips),
            queries_executed=queries_executed,
            search_timestamp=datetime.utcnow().isoformat()
        )
    
    async def search_batch(
        self,
        cues: List[VisualCue],
        max_results_per_query: int = 5,
        job_id: Optional[str] = None,
        progress_callback: Optional[callable] = None
    ) -> List[SearchResult]:
        """
        Search for multiple cues in batch.
        
        Args:
            cues: List of visual cues to search for
            max_results_per_query: Maximum results per search query
            job_id: Optional job ID for tracking
            progress_callback: Optional callback for progress updates
            
        Returns:
            List of SearchResults, one per cue
        """
        results = []
        total_cues = len(cues)
        
        for idx, cue in enumerate(cues):
            result = await self.search_for_cue(
                cue=cue,
                max_results_per_query=max_results_per_query,
                job_id=job_id
            )
            results.append(result)
            
            if progress_callback:
                await progress_callback(
                    current=idx + 1,
                    total=total_cues,
                    message=f"Searched {idx + 1}/{total_cues} cues"
                )
        
        return results
    
    async def _search_pexels(
        self,
        query: str,
        per_page: int = 10,
        page: int = 1,
        orientation: str = "landscape",
        size: str = "medium"
    ) -> List[StockClip]:
        """
        Search Pexels API for videos.
        
        Args:
            query: Search query string
            per_page: Results per page (max 80)
            page: Page number
            orientation: landscape, portrait, or square
            size: large, medium, or small
            
        Returns:
            List of StockClip objects
        """
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                self.pexels_base_url,
                headers={"Authorization": self.pexels_api_key},
                params={
                    "query": query,
                    "per_page": min(per_page, 80),
                    "page": page,
                    "orientation": orientation,
                    "size": size
                }
            )
            response.raise_for_status()
            data = response.json()
            
            clips = []
            for video in data.get("videos", []):
                # Find best video file (prefer HD)
                video_files = video.get("video_files", [])
                best_file = self._select_best_quality(video_files)
                
                if not best_file:
                    continue
                
                # Find preview image
                video_pictures = video.get("video_pictures", [])
                preview_url = video_pictures[0]["picture"] if video_pictures else None
                
                clip = StockClip(
                    id=str(video["id"]),
                    source="pexels",
                    title=query,  # Pexels doesn't provide titles
                    description=video.get("url", ""),
                    duration=video.get("duration", 0),
                    preview_url=preview_url,
                    download_url=best_file["link"],
                    width=best_file.get("width", 1920),
                    height=best_file.get("height", 1080),
                    fps=best_file.get("fps", 30),
                    file_type=best_file.get("file_type", "video/mp4"),
                    author=video.get("user", {}).get("name", "Unknown"),
                    author_url=video.get("user", {}).get("url", ""),
                    license="Pexels License",
                    tags=[],
                    quality=self._determine_quality(best_file)
                )
                clips.append(clip)
            
            return clips
    
    def _select_best_quality(self, video_files: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """Select the best quality video file, preferring HD."""
        if not video_files:
            return None
        
        # Sort by height (resolution) descending
        sorted_files = sorted(
            video_files,
            key=lambda x: x.get("height", 0),
            reverse=True
        )
        
        # Prefer HD (720p-1080p) over 4K for reasonable file sizes
        for file in sorted_files:
            height = file.get("height", 0)
            if 720 <= height <= 1080:
                return file
        
        # Fall back to highest quality
        return sorted_files[0] if sorted_files else None
    
    def _determine_quality(self, video_file: Dict[str, Any]) -> str:
        """Determine quality label based on resolution."""
        height = video_file.get("height", 0)
        if height >= 2160:
            return "4K"
        elif height >= 1080:
            return "1080p"
        elif height >= 720:
            return "720p"
        elif height >= 480:
            return "480p"
        else:
            return "SD"
    
    def _deduplicate_clips(self, clips: List[StockClip]) -> List[StockClip]:
        """Remove duplicate clips based on video ID."""
        seen_ids = set()
        unique_clips = []
        
        for clip in clips:
            if clip.id not in seen_ids:
                seen_ids.add(clip.id)
                unique_clips.append(clip)
        
        return unique_clips
    
    async def _rate_limit(self):
        """Implement rate limiting for API calls."""
        import time
        current_time = time.time()
        elapsed = current_time - self._last_request_time
        
        if elapsed < self.rate_limit_delay:
            await asyncio.sleep(self.rate_limit_delay - elapsed)
        
        self._last_request_time = time.time()


# Singleton instance
searcher_service = SearcherService()
