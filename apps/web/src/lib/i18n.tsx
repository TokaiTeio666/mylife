"use client";

/**
 * Locale provider + `useCopy()` (AC-63).
 *
 * Persists the choice in a cookie (so server rendering and `lang` agree) and
 * keeps `<html lang>` in sync. Default is zh-CN.
 */
import * as React from "react";

import {
  copy as zhCopy,
  DEFAULT_LOCALE,
  getCopy,
  isLocale,
  LOCALE_COOKIE,
  LOCALES,
  type Copy,
  type Locale,
} from "@/lib/copy";

type LocaleContextValue = {
  locale: Locale;
  copy: Copy;
  setLocale: (locale: Locale) => void;
  locales: readonly Locale[];
};

const LocaleContext = React.createContext<LocaleContextValue>({
  locale: DEFAULT_LOCALE,
  copy: zhCopy,
  setLocale: () => {},
  locales: LOCALES,
});

function readCookieLocale(): Locale {
  if (typeof document === "undefined") return DEFAULT_LOCALE;
  const prefix = `${LOCALE_COOKIE}=`;
  const hit = document.cookie
    .split("; ")
    .find((row) => row.startsWith(prefix));
  const value = hit ? decodeURIComponent(hit.slice(prefix.length)) : null;
  return isLocale(value) ? value : DEFAULT_LOCALE;
}

export function LocaleProvider({ children }: { children: React.ReactNode }) {
  const [locale, setLocaleState] = React.useState<Locale>(DEFAULT_LOCALE);

  React.useEffect(() => {
    // 首屏由 cookie 决定；SSR 无法读取 client cookie，故挂载后同步一次。
    setLocaleState(readCookieLocale());
  }, []);

  React.useEffect(() => {
    document.documentElement.lang = locale;
  }, [locale]);

  const setLocale = React.useCallback((next: Locale) => {
    document.cookie = `${LOCALE_COOKIE}=${encodeURIComponent(next)}; path=/; max-age=31536000; samesite=lax`;
    setLocaleState(next);
  }, []);

  const value = React.useMemo<LocaleContextValue>(
    () => ({ locale, copy: getCopy(locale), setLocale, locales: LOCALES }),
    [locale, setLocale],
  );

  return <LocaleContext.Provider value={value}>{children}</LocaleContext.Provider>;
}

export function useCopy(): Copy {
  return React.useContext(LocaleContext).copy;
}

export function useLocale(): LocaleContextValue {
  return React.useContext(LocaleContext);
}
