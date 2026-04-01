import { NextRequest, NextResponse } from 'next/server'

// In-memory settings storage
let settings = {
  openaiKey: '',
  pexelsKey: '',
  defaultStyle: 'documentary',
  defaultAudience: 'general',
  defaultQuality: '1080p',
  resultsPerCue: 5,
  monthlyBudget: 50,
  budgetAlerts: true,
  alertThreshold: 75,
}

// GET /api/v1/settings - Get current settings
export async function GET() {
  // Return settings without sensitive keys
  return NextResponse.json({
    ...settings,
    openaiKey: settings.openaiKey ? '****' + settings.openaiKey.slice(-4) : '',
    pexelsKey: settings.pexelsKey ? '****' + settings.pexelsKey.slice(-4) : '',
  })
}

// PUT /api/v1/settings - Update settings
export async function PUT(request: NextRequest) {
  try {
    const body = await request.json()
    
    settings = {
      ...settings,
      ...body,
    }
    
    return NextResponse.json({ message: 'Settings updated' })
  } catch (error) {
    console.error('Error updating settings:', error)
    return NextResponse.json(
      { detail: 'Failed to update settings' },
      { status: 500 }
    )
  }
}

export { settings }
