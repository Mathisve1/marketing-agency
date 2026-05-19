import * as React from "react";

export const Textarea = React.forwardRef<
  HTMLTextAreaElement,
  React.TextareaHTMLAttributes<HTMLTextAreaElement>
>(function Textarea({ className = "", ...rest }, ref) {
  return (
    <textarea
      ref={ref}
      className={`w-full min-h-[96px] rounded-md border border-[color:var(--color-hairline)] bg-white px-3 py-2 text-sm leading-6 outline-none focus:border-[color:var(--color-accent)] ${className}`}
      {...rest}
    />
  );
});
