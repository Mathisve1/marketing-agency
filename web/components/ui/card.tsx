import * as React from "react";

export function Card({
  className = "",
  ...rest
}: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={`rounded-lg border border-[color:var(--color-hairline)] bg-white shadow-[0_1px_2px_rgba(0,0,0,0.04)] ${className}`}
      {...rest}
    />
  );
}

export function CardHeader({
  className = "",
  ...rest
}: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={`p-5 border-b border-[color:var(--color-hairline)] ${className}`} {...rest} />;
}

export function CardTitle({
  className = "",
  ...rest
}: React.HTMLAttributes<HTMLHeadingElement>) {
  return <h3 className={`text-lg font-semibold ${className}`} {...rest} />;
}

export function CardBody({
  className = "",
  ...rest
}: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={`p-5 ${className}`} {...rest} />;
}

export function CardFooter({
  className = "",
  ...rest
}: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={`p-5 border-t border-[color:var(--color-hairline)] ${className}`} {...rest} />;
}
