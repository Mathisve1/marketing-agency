import * as React from "react";

type Tone = "neutral" | "success" | "warn" | "danger" | "info";

const TONE: Record<Tone, string> = {
  neutral:
    "bg-[color:var(--color-hairline)] text-[color:var(--color-ink)]",
  success:
    "bg-[color:var(--color-success)]/12 text-[color:var(--color-success)]",
  warn: "bg-[color:var(--color-warn)]/15 text-[color:var(--color-warn)]",
  danger: "bg-[color:var(--color-danger)]/15 text-[color:var(--color-danger)]",
  info: "bg-[color:var(--color-accent)]/15 text-[color:var(--color-accent)]",
};

export interface BadgeProps extends React.HTMLAttributes<HTMLSpanElement> {
  tone?: Tone;
}

export function Badge({ tone = "neutral", className = "", ...rest }: BadgeProps) {
  return (
    <span
      className={`inline-flex items-center px-2.5 py-1 rounded-full text-[11px] font-semibold tracking-[0.06em] uppercase ${TONE[tone]} ${className}`}
      {...rest}
    />
  );
}
