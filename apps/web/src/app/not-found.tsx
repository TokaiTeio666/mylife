import Link from "next/link";

import { copy } from "@/lib/copy/zh-CN";

export default function NotFound() {
  return (
    <div className="flex min-h-screen flex-col items-center justify-center gap-3 p-6 text-center">
      <p className="text-h1 font-semibold">{copy.common.notFound}</p>
      <Link
        href="/dashboard"
        className="text-small text-primary underline-offset-4 hover:underline"
      >
        {copy.common.backHome}
      </Link>
    </div>
  );
}
