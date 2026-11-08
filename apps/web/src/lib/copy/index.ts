/**
 * Locale registry (AC-63). `zh-CN` is the source of truth for keys; other
 * locales are deep-merged over it so a partially translated page never shows
 * `undefined`.
 */
import { enUS } from "./en-US";
import { copy as zhCN } from "./zh-CN";

export type Copy = typeof zhCN;

export const LOCALES = ["zh-CN", "en-US"] as const;
export type Locale = (typeof LOCALES)[number];
export const DEFAULT_LOCALE: Locale = "zh-CN";
export const LOCALE_COOKIE = "mylife_locale";

type Dict = Record<string, unknown>;

function isPlainObject(value: unknown): value is Dict {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function deepMerge<T>(base: T, override: unknown): T {
  if (!isPlainObject(override)) return base;
  const merged: Dict = { ...(base as Dict) };
  for (const [key, value] of Object.entries(override)) {
    const current = merged[key];
    merged[key] = isPlainObject(current) && isPlainObject(value)
      ? deepMerge(current, value)
      : value;
  }
  return merged as T;
}

const DICTIONARIES: Record<Locale, Copy> = {
  "zh-CN": zhCN,
  "en-US": deepMerge(zhCN, enUS),
};

export function getCopy(locale: Locale): Copy {
  return DICTIONARIES[locale] ?? DICTIONARIES[DEFAULT_LOCALE];
}

export function isLocale(value: string | null | undefined): value is Locale {
  return !!value && (LOCALES as readonly string[]).includes(value);
}

//: 服务端组件只能拿到默认语言；客户端经 `useCopy()` 取当前语言。
export const copy = zhCN;
