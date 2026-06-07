/**
 * N0 scaffold test — confirms Vite + React + Tailwind render without errors.
 */
import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import App from './App'

describe('App scaffold', () => {
  it('renders the dashboard heading', () => {
    render(<App />)
    expect(screen.getByText('CCBT Dashboard v2')).toBeTruthy()
  })

  it('renders the scaffold subtitle', () => {
    render(<App />)
    expect(screen.getByText(/scaffold/i)).toBeTruthy()
  })
})
