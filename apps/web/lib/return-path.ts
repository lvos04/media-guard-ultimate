/** Contract 1.3 §4.1: blijf op een veilig lokaal terugkeerpad. */
export function returnPath(value: string | null): string {
  if (!value || value.length > 256 || !/^\/(?![/\\])[!-~]*$/.test(value) || value.includes("://") || value.includes("\\")) {
    return "/";
  }

  try {
    // Controleer alleen het pad en geef een geldige bestemming ongewijzigd terug.
    let path = value.split(/[?#]/, 1)[0];
    for (let round = 0; round < 4 && path.includes("%"); round++) {
      path = decodeURIComponent(path);
    }
    if (path.includes("%") || path.startsWith("//") || path.includes("\\") || path.includes("://")) {
      return "/";
    }

    const normalized = new URL("http://local.invalid");
    normalized.pathname = path;
    const { pathname } = normalized;
    if (pathname.startsWith("//") || /^\/(?:login|logout|api\/+v1\/+auth\/+logout)\/*$/i.test(pathname)) {
      return "/";
    }
  } catch {
    return "/";
  }
  return value;
}
