"use client";

import * as React from "react";
import { signOut } from "@/lib/actions/auth";

interface Props {
  label?: string;
  className?: string;
}

export function LogoutButton({ label = "Sign out", className = "" }: Props) {
  const [pending, startTransition] = React.useTransition();
  return (
    <button
      type="button"
      disabled={pending}
      className={`text-sm text-[color:var(--color-ink-muted)] hover:text-[color:var(--color-ink)] disabled:opacity-50 ${className}`}
      onClick={() => startTransition(() => signOut())}
    >
      {pending ? "Signing out…" : label}
    </button>
  );
}
