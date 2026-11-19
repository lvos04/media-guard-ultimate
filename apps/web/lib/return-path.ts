/** Contract 1.3 §4.1: blijf op een veilig lokaal terugkeerpad. */
export function returnPath(value: string | null): string {
  if (!value || value.length > 256 || !/^\/(?![/\\])[!-~]*$/.test(value) || value.includes("://") || value.includes("\\")) {
    return "/";
  }

  try {
    const path = decodeURIComponent(new URL(value, "http://local.invalid").pathname);
    if (/^\/login\/*$/i.test(path)) return "/";
  } catch {
    // Ongeldige procentcodering verandert het pad niet in de loginroute.
  }
  return value;
}
