import type { Metadata } from "next";

import { AuthForm } from "@/components/auth-form";
import { copy } from "@/lib/copy";

export const metadata: Metadata = { title: copy.auth.register.submit };

export default function RegisterPage() {
  return <AuthForm mode="register" />;
}
