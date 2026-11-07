"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import * as React from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ApiError, api } from "@/lib/api/client";
import { copy } from "@/lib/copy/zh-CN";

type Mode = "login" | "register";

function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    const known = (copy.auth.errors as Record<string, string>)[error.code];
    return known ?? error.message ?? copy.auth.errors.default;
  }
  return copy.auth.errors.default;
}

export function AuthForm({ mode }: { mode: Mode }) {
  const isLogin = mode === "login";
  const t = isLogin ? copy.auth.login : copy.auth.register;
  const router = useRouter();
  const queryClient = useQueryClient();

  const [email, setEmail] = React.useState("");
  const [password, setPassword] = React.useState("");
  const [displayName, setDisplayName] = React.useState("");
  const [inviteCode, setInviteCode] = React.useState("");

  const mutation = useMutation({
    mutationFn: async () => {
      if (isLogin) {
        return api.login({ email, password });
      }
      return api.register({
        email,
        password,
        display_name: displayName,
        invite_code: inviteCode,
      });
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["auth", "me"] });
      router.replace("/dashboard");
    },
  });

  return (
    <form
      className="flex w-full max-w-sm flex-col gap-5"
      onSubmit={(event) => {
        event.preventDefault();
        mutation.mutate();
      }}
      noValidate
    >
      <div className="flex flex-col gap-1">
        <h1 className="text-display font-semibold tracking-tight">{t.title}</h1>
        <p className="text-small text-muted-foreground">{t.subtitle}</p>
      </div>

      {!isLogin ? (
        <div className="flex flex-col gap-2">
          <Label htmlFor="display_name">{copy.auth.register.displayName}</Label>
          <Input
            id="display_name"
            name="display_name"
            autoComplete="name"
            required
            maxLength={64}
            value={displayName}
            onChange={(e) => setDisplayName(e.target.value)}
          />
        </div>
      ) : null}

      <div className="flex flex-col gap-2">
        <Label htmlFor="email">{copy.auth.login.email}</Label>
        <Input
          id="email"
          name="email"
          type="email"
          inputMode="email"
          autoComplete="email"
          required
          maxLength={254}
          value={email}
          onChange={(e) => setEmail(e.target.value)}
        />
      </div>

      <div className="flex flex-col gap-2">
        <Label htmlFor="password">{copy.auth.login.password}</Label>
        <Input
          id="password"
          name="password"
          type="password"
          autoComplete={isLogin ? "current-password" : "new-password"}
          required
          minLength={isLogin ? 1 : 8}
          maxLength={128}
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          aria-describedby={!isLogin ? "password-hint" : undefined}
        />
        {!isLogin ? (
          <p id="password-hint" className="text-caption text-muted-foreground">
            {copy.auth.register.passwordHint}
          </p>
        ) : null}
      </div>

      {!isLogin ? (
        <div className="flex flex-col gap-2">
          <Label htmlFor="invite_code">{copy.auth.register.inviteCode}</Label>
          <Input
            id="invite_code"
            name="invite_code"
            autoComplete="off"
            required
            minLength={8}
            maxLength={32}
            value={inviteCode}
            onChange={(e) => setInviteCode(e.target.value)}
          />
        </div>
      ) : null}

      {mutation.error ? (
        <p role="alert" className="text-small text-danger">
          {errorMessage(mutation.error)}
        </p>
      ) : null}

      <Button type="submit" size="lg" disabled={mutation.isPending}>
        {mutation.isPending ? t.submitting : t.submit}
      </Button>

      <p className="text-small text-muted-foreground">
        {isLogin ? (
          <Link href="/register" className="text-primary underline-offset-4 hover:underline">
            {copy.auth.login.noAccount}
          </Link>
        ) : (
          <Link href="/login" className="text-primary underline-offset-4 hover:underline">
            {copy.auth.register.hasAccount}
          </Link>
        )}
      </p>
    </form>
  );
}
