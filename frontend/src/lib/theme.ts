/**
 * Light/dark theme, applied by toggling the `dark` class on <html> (the class
 * shadcn's `@custom-variant dark` keys off). The initial class is set by an
 * inline script in index.html to avoid a flash; this module keeps it in sync at
 * runtime and persists the choice.
 */

export type Theme = 'light' | 'dark'

const STORAGE_KEY = 'theme'

/** The theme currently applied to the document. */
export function currentTheme(): Theme {
  return document.documentElement.classList.contains('dark') ? 'dark' : 'light'
}

/** Apply a theme to the document and remember it. */
export function setTheme(theme: Theme): void {
  document.documentElement.classList.toggle('dark', theme === 'dark')
  try {
    localStorage.setItem(STORAGE_KEY, theme)
  } catch {
    // private mode / storage disabled — the theme still applies for this session
  }
}
