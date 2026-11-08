import Link from "next/link";

import { copy } from "@/lib/copy/zh-CN";

export default function AuthLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <div className="grid min-h-screen lg:grid-cols-2">
      <div className="hidden flex-col justify-between bg-surface p-10 lg:flex">
        <Link href="/" className="text-h1 font-semibold tracking-tight">
          {copy.app.name}
        </Link>
        <div className="max-w-md">
          <p className="text-display font-semibold leading-tight">
            {copy.app.tagline}
          </p>
          <p className="mt-3 text-body text-muted-foreground">
            {copy.app.description}
          </p>
        </div>
        <p className="text-caption text-muted-foreground">
          {copy.app.footerTagline}
        </p>
      </div>

      <div className="flex items-center justify-center p-6">
        <div className="w-full max-w-sm">
          <Link
            href="/"
            className="mb-8 inline-block text-h2 font-semibold tracking-tight lg:hidden"
          >
            {copy.app.name}
          </Link>
          {children}
        </div>
      </div>
    </div>
  );
}
