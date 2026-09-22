export function announce(message: string) { window.dispatchEvent(new CustomEvent<string>('intelitex:notice', { detail: message })) }
