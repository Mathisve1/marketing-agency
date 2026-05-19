// Minimal shadcn-compatible Button. Hand-written so Phase 1A doesn't pull
// shadcn-cli + radix yet — but the API matches `shadcn/ui` so the operator
// can swap in the canonical implementation later without rewriting calls.

import * as React from "react";

type Variant = "primary" | "secondary" | "ghost" | "danger" | "success";
type Size = "sm" | "md" | "lg";

const VARIANT: Record<Variant, string> = {
  primary:
    "bg-[color:var(--color-accent)] text-[color:var(--color-cream)] hover:opacity-90",
  secondary:
    "bg-[color:var(--color-cream)] text-[color:var(--color-ink)] border border-[color:var(--color-hairline)] hover:bg-[color:var(--color-cream-soft)]",
  ghost:
    "bg-transparent text-[color:var(--color-ink)] hover:bg-[color:var(--color-hairline)]",
  danger:
    "bg-[color:var(--color-danger)] text-white hover:opacity-90",
  success:
    "bg-[color:var(--color-success)] text-white hover:opacity-90",
};

const SIZE: Record<Size, string> = {
  sm: "h-8 px-3 text-sm rounded-md",
  md: "h-10 px-4 text-sm rounded-md",
  lg: "h-12 px-5 text-base rounded-md",
};

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
}

export function Button({
  variant = "primary",
  size = "md",
  className = "",
  ...rest
}: ButtonProps) {
  const cls = [
    "inline-flex items-center justify-center font-medium",
    "disabled:opacity-50 disabled:cursor-not-allowed transition-opacity",
    VARIANT[variant],
    SIZE[size],
    className,
  ].join(" ");
  return <button className={cls} {...rest} />;
}
