import type { Metadata } from "next";

import { AuthForm } from "@/components/auth-form";
import { copy } from "@/lib/copy";

export const metadata: Metadata = { title: copy.auth.login.submit };

export default function LoginPage() {
  return <AuthForm mode="login" />;
}
