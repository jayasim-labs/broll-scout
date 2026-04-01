"use client"

import { useState, useEffect } from "react"
import { toast } from "sonner"
import { Key, SlidersHorizontal, DollarSign, Database, Eye, EyeOff, Download, Trash2, RotateCcw } from "lucide-react"
import { Navbar } from "@/components/navbar"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Switch } from "@/components/ui/switch"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog"
import type { Settings, CostData } from "@/lib/types"

const API_BASE = '/api/v1'

const defaultSettings: Settings = {
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

export default function SettingsPage() {
  const [settings, setSettings] = useState<Settings>(defaultSettings)
  const [showOpenaiKey, setShowOpenaiKey] = useState(false)
  const [showPexelsKey, setShowPexelsKey] = useState(false)
  const [testingOpenai, setTestingOpenai] = useState(false)
  const [testingPexels, setTestingPexels] = useState(false)
  const [costData, setCostData] = useState<CostData | null>(null)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    loadSettings()
    loadCostData()
  }, [])

  const loadSettings = () => {
    const saved = localStorage.getItem('broll-settings')
    if (saved) {
      try {
        const parsed = JSON.parse(saved)
        setSettings({ ...defaultSettings, ...parsed })
      } catch {
        // Use defaults
      }
    }
  }

  const loadCostData = async () => {
    try {
      const response = await fetch(`${API_BASE}/costs/monthly`)
      if (response.ok) {
        const data = await response.json()
        setCostData(data)
      }
    } catch (error) {
      console.error('Failed to load cost data:', error)
    }
  }

  const updateSetting = <K extends keyof Settings>(key: K, value: Settings[K]) => {
    setSettings(prev => ({ ...prev, [key]: value }))
  }

  const testApiKey = async (provider: 'openai' | 'pexels') => {
    const key = provider === 'openai' ? settings.openaiKey : settings.pexelsKey
    if (!key.trim()) {
      toast.error('Please enter an API key')
      return
    }

    if (provider === 'openai') {
      setTestingOpenai(true)
    } else {
      setTestingPexels(true)
    }

    try {
      const response = await fetch(`${API_BASE}/settings/test-key`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ provider, key })
      })

      const result = await response.json()

      if (result.valid) {
        toast.success('API key is valid')
      } else {
        toast.error(result.error || 'Invalid API key')
      }
    } catch {
      toast.error('Failed to test API key')
    } finally {
      if (provider === 'openai') {
        setTestingOpenai(false)
      } else {
        setTestingPexels(false)
      }
    }
  }

  const saveSettings = async () => {
    setSaving(true)
    
    try {
      localStorage.setItem('broll-settings', JSON.stringify(settings))
      
      await fetch(`${API_BASE}/settings`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(settings)
      })

      toast.success('Settings saved')
    } catch {
      toast.info('Settings saved locally')
    } finally {
      setSaving(false)
    }
  }

  const exportAllData = async () => {
    try {
      const response = await fetch(`${API_BASE}/data/export`)
      if (!response.ok) throw new Error('Export failed')

      const data = await response.json()
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' })
      const url = URL.createObjectURL(blob)

      const a = document.createElement('a')
      a.href = url
      a.download = `broll-scout-export-${new Date().toISOString().split('T')[0]}.json`
      a.click()

      URL.revokeObjectURL(url)
      toast.success('Data exported')
    } catch {
      toast.error('Failed to export data')
    }
  }

  const clearJobHistory = async () => {
    try {
      await fetch(`${API_BASE}/jobs`, { method: 'DELETE' })
      toast.success('Job history cleared')
    } catch {
      toast.error('Failed to clear history')
    }
  }

  const resetSettings = () => {
    const newSettings = {
      ...defaultSettings,
      openaiKey: settings.openaiKey,
      pexelsKey: settings.pexelsKey,
    }
    setSettings(newSettings)
    localStorage.setItem('broll-settings', JSON.stringify(newSettings))
    toast.success('Settings reset to defaults')
  }

  const spendPercent = costData && settings.monthlyBudget > 0
    ? Math.round((costData.total_cost / settings.monthlyBudget) * 100)
    : 0

  return (
    <div className="min-h-screen bg-background">
      <Navbar />

      <main className="max-w-4xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
        <div className="space-y-8">
          {/* API Keys */}
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Key className="w-5 h-5 text-primary" />
                API Keys
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-6">
              {/* OpenAI */}
              <div>
                <Label htmlFor="openai-key" className="text-muted-foreground">
                  OpenAI API Key
                </Label>
                <div className="flex gap-2 mt-2">
                  <div className="relative flex-1">
                    <Input
                      id="openai-key"
                      type={showOpenaiKey ? 'text' : 'password'}
                      value={settings.openaiKey}
                      onChange={(e) => updateSetting('openaiKey', e.target.value)}
                      placeholder="sk-..."
                      className="pr-10 bg-secondary"
                    />
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon"
                      className="absolute right-0 top-0 h-full"
                      onClick={() => setShowOpenaiKey(!showOpenaiKey)}
                    >
                      {showOpenaiKey ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                    </Button>
                  </div>
                  <Button
                    variant="secondary"
                    onClick={() => testApiKey('openai')}
                    disabled={testingOpenai}
                  >
                    {testingOpenai ? 'Testing...' : 'Test'}
                  </Button>
                </div>
                <p className="text-xs text-muted-foreground mt-1">
                  Used for script analysis and clip matching.{' '}
                  <a href="https://platform.openai.com/api-keys" target="_blank" rel="noopener noreferrer" className="text-primary hover:underline">
                    Get API key
                  </a>
                </p>
              </div>

              {/* Pexels */}
              <div>
                <Label htmlFor="pexels-key" className="text-muted-foreground">
                  Pexels API Key
                </Label>
                <div className="flex gap-2 mt-2">
                  <div className="relative flex-1">
                    <Input
                      id="pexels-key"
                      type={showPexelsKey ? 'text' : 'password'}
                      value={settings.pexelsKey}
                      onChange={(e) => updateSetting('pexelsKey', e.target.value)}
                      placeholder="Enter your Pexels API key..."
                      className="pr-10 bg-secondary"
                    />
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon"
                      className="absolute right-0 top-0 h-full"
                      onClick={() => setShowPexelsKey(!showPexelsKey)}
                    >
                      {showPexelsKey ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                    </Button>
                  </div>
                  <Button
                    variant="secondary"
                    onClick={() => testApiKey('pexels')}
                    disabled={testingPexels}
                  >
                    {testingPexels ? 'Testing...' : 'Test'}
                  </Button>
                </div>
                <p className="text-xs text-muted-foreground mt-1">
                  Used for searching stock footage.{' '}
                  <a href="https://www.pexels.com/api/" target="_blank" rel="noopener noreferrer" className="text-primary hover:underline">
                    Get API key
                  </a>
                </p>
              </div>
            </CardContent>
          </Card>

          {/* Default Preferences */}
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <SlidersHorizontal className="w-5 h-5 text-primary" />
                Default Preferences
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                <div>
                  <Label className="text-muted-foreground">Default Video Style</Label>
                  <Select value={settings.defaultStyle} onValueChange={(v) => updateSetting('defaultStyle', v)}>
                    <SelectTrigger className="mt-2 bg-secondary">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="documentary">Documentary</SelectItem>
                      <SelectItem value="corporate">Corporate</SelectItem>
                      <SelectItem value="educational">Educational</SelectItem>
                      <SelectItem value="cinematic">Cinematic</SelectItem>
                      <SelectItem value="social-media">Social Media</SelectItem>
                      <SelectItem value="news">News/Journalism</SelectItem>
                    </SelectContent>
                  </Select>
                </div>

                <div>
                  <Label className="text-muted-foreground">Default Target Audience</Label>
                  <Select value={settings.defaultAudience} onValueChange={(v) => updateSetting('defaultAudience', v)}>
                    <SelectTrigger className="mt-2 bg-secondary">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="general">General</SelectItem>
                      <SelectItem value="professional">Professional/Business</SelectItem>
                      <SelectItem value="young-adult">Young Adult (18-35)</SelectItem>
                      <SelectItem value="tech-savvy">Tech Savvy</SelectItem>
                      <SelectItem value="academic">Academic</SelectItem>
                    </SelectContent>
                  </Select>
                </div>

                <div>
                  <Label className="text-muted-foreground">Default Minimum Quality</Label>
                  <Select value={settings.defaultQuality} onValueChange={(v) => updateSetting('defaultQuality', v)}>
                    <SelectTrigger className="mt-2 bg-secondary">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="720p">720p (HD)</SelectItem>
                      <SelectItem value="1080p">1080p (Full HD)</SelectItem>
                      <SelectItem value="4K">4K (Ultra HD)</SelectItem>
                    </SelectContent>
                  </Select>
                </div>

                <div>
                  <Label className="text-muted-foreground">Results Per Cue</Label>
                  <Select value={String(settings.resultsPerCue)} onValueChange={(v) => updateSetting('resultsPerCue', Number(v))}>
                    <SelectTrigger className="mt-2 bg-secondary">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="3">3 clips</SelectItem>
                      <SelectItem value="5">5 clips</SelectItem>
                      <SelectItem value="10">10 clips</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
              </div>
            </CardContent>
          </Card>

          {/* Cost Tracking */}
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <DollarSign className="w-5 h-5 text-primary" />
                Cost Tracking
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="flex items-center justify-between p-4 bg-secondary rounded-lg">
                <div>
                  <p className="font-medium">Monthly Budget</p>
                  <p className="text-sm text-muted-foreground">Set a spending limit for API costs</p>
                </div>
                <div className="flex items-center gap-2">
                  <span className="text-muted-foreground">$</span>
                  <Input
                    type="number"
                    value={settings.monthlyBudget}
                    onChange={(e) => updateSetting('monthlyBudget', Number(e.target.value))}
                    min={0}
                    step={5}
                    className="w-24 bg-card"
                  />
                </div>
              </div>

              <div className="flex items-center justify-between p-4 bg-secondary rounded-lg">
                <div>
                  <p className="font-medium">Current Month Spend</p>
                  <p className="text-sm text-muted-foreground">Total API costs this month</p>
                </div>
                <div className="text-right">
                  <p className="text-xl font-bold text-accent">
                    ${(costData?.total_cost || 0).toFixed(2)}
                  </p>
                  <p className="text-sm text-muted-foreground">{spendPercent}% of budget</p>
                </div>
              </div>

              <div className="flex items-center justify-between p-4 bg-secondary rounded-lg">
                <div className="flex items-center gap-3">
                  <Switch
                    id="budget-alerts"
                    checked={settings.budgetAlerts}
                    onCheckedChange={(checked) => updateSetting('budgetAlerts', checked)}
                  />
                  <div>
                    <Label htmlFor="budget-alerts" className="font-medium cursor-pointer">Budget Alerts</Label>
                    <p className="text-sm text-muted-foreground">Warn when approaching budget limit</p>
                  </div>
                </div>
                <Select
                  value={String(settings.alertThreshold)}
                  onValueChange={(v) => updateSetting('alertThreshold', Number(v))}
                >
                  <SelectTrigger className="w-28 bg-card">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="50">At 50%</SelectItem>
                    <SelectItem value="75">At 75%</SelectItem>
                    <SelectItem value="90">At 90%</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </CardContent>
          </Card>

          {/* Data Management */}
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Database className="w-5 h-5 text-primary" />
                Data Management
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="flex items-center justify-between p-4 bg-secondary rounded-lg">
                <div>
                  <p className="font-medium">Export All Data</p>
                  <p className="text-sm text-muted-foreground">Download all jobs and settings as JSON</p>
                </div>
                <Button onClick={exportAllData} className="gap-2">
                  <Download className="w-4 h-4" />
                  Export
                </Button>
              </div>

              <div className="flex items-center justify-between p-4 bg-secondary rounded-lg">
                <div>
                  <p className="font-medium">Clear Job History</p>
                  <p className="text-sm text-muted-foreground">Remove all completed jobs (keeps settings)</p>
                </div>
                <AlertDialog>
                  <AlertDialogTrigger asChild>
                    <Button variant="destructive" className="gap-2">
                      <Trash2 className="w-4 h-4" />
                      Clear
                    </Button>
                  </AlertDialogTrigger>
                  <AlertDialogContent>
                    <AlertDialogHeader>
                      <AlertDialogTitle>Clear Job History?</AlertDialogTitle>
                      <AlertDialogDescription>
                        This will permanently delete all job history. This action cannot be undone.
                      </AlertDialogDescription>
                    </AlertDialogHeader>
                    <AlertDialogFooter>
                      <AlertDialogCancel>Cancel</AlertDialogCancel>
                      <AlertDialogAction onClick={clearJobHistory}>Clear History</AlertDialogAction>
                    </AlertDialogFooter>
                  </AlertDialogContent>
                </AlertDialog>
              </div>

              <div className="flex items-center justify-between p-4 bg-secondary rounded-lg">
                <div>
                  <p className="font-medium">Reset All Settings</p>
                  <p className="text-sm text-muted-foreground">Reset everything to defaults (keeps API keys)</p>
                </div>
                <AlertDialog>
                  <AlertDialogTrigger asChild>
                    <Button variant="outline" className="gap-2 border-destructive text-destructive hover:bg-destructive hover:text-destructive-foreground">
                      <RotateCcw className="w-4 h-4" />
                      Reset
                    </Button>
                  </AlertDialogTrigger>
                  <AlertDialogContent>
                    <AlertDialogHeader>
                      <AlertDialogTitle>Reset Settings?</AlertDialogTitle>
                      <AlertDialogDescription>
                        This will reset all settings to their default values. Your API keys will be preserved.
                      </AlertDialogDescription>
                    </AlertDialogHeader>
                    <AlertDialogFooter>
                      <AlertDialogCancel>Cancel</AlertDialogCancel>
                      <AlertDialogAction onClick={resetSettings}>Reset Settings</AlertDialogAction>
                    </AlertDialogFooter>
                  </AlertDialogContent>
                </AlertDialog>
              </div>
            </CardContent>
          </Card>

          {/* Save Button */}
          <div className="flex justify-end gap-4">
            <Button variant="secondary" asChild>
              <a href="/">Cancel</a>
            </Button>
            <Button onClick={saveSettings} disabled={saving}>
              {saving ? 'Saving...' : 'Save Settings'}
            </Button>
          </div>
        </div>
      </main>
    </div>
  )
}
