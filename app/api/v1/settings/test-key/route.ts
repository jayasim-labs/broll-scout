import { NextRequest, NextResponse } from 'next/server'

// POST /api/v1/settings/test-key - Test an API key
export async function POST(request: NextRequest) {
  try {
    const { provider, key } = await request.json()
    
    if (!key || !key.trim()) {
      return NextResponse.json({
        valid: false,
        error: 'API key is required'
      })
    }
    
    // Simulate API key validation
    // In production, this would actually test the key against the provider
    if (provider === 'openai') {
      // Check if it looks like an OpenAI key
      if (key.startsWith('sk-') && key.length > 20) {
        return NextResponse.json({
          valid: true,
          message: 'OpenAI API key is valid'
        })
      } else {
        return NextResponse.json({
          valid: false,
          error: 'Invalid OpenAI API key format'
        })
      }
    }
    
    if (provider === 'pexels') {
      // Pexels keys are typically 56 characters
      if (key.length >= 20) {
        return NextResponse.json({
          valid: true,
          message: 'Pexels API key is valid'
        })
      } else {
        return NextResponse.json({
          valid: false,
          error: 'Invalid Pexels API key format'
        })
      }
    }
    
    return NextResponse.json({
      valid: false,
      error: 'Unknown provider'
    })
  } catch (error) {
    console.error('Error testing API key:', error)
    return NextResponse.json({
      valid: false,
      error: 'Failed to test API key'
    })
  }
}
