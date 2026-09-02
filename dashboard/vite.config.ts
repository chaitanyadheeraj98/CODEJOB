import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  test: {
    // Tests that mount App render a ~7,500-line component in jsdom. Alone each
    // takes 400-1000ms, but the suite runs files in parallel and under load
    // several reliably blew the 5s default while passing at --maxWorkers=2.
    // The tests are not slow; the default is too tight for this suite, and it
    // gets tighter with every file added.
    testTimeout: 20000,
    hookTimeout: 20000,
  },
})
