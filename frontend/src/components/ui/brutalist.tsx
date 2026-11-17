import * as React from "react";
import { cn } from "@/lib/utils";

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: "default" | "outline" | "secondary" | "danger" | "ghost";
  size?: "sm" | "md" | "lg";
}

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant = "default", size = "md", ...props }, ref) => {
    const variants: Record<NonNullable<ButtonProps["variant"]>, string> = {
      default:
        "bg-black text-white border-2 border-black shadow-brutal hover:bg-white hover:text-black active:translate-x-[2px] active:translate-y-[2px] active:shadow-none",
      outline:
        "bg-white text-black border-2 border-black shadow-brutal-sm hover:bg-black hover:text-white active:translate-x-[2px] active:translate-y-[2px] active:shadow-none",
      secondary:
        "bg-[#E5E5E0] text-black border-2 border-black shadow-brutal-sm hover:bg-black hover:text-white active:translate-x-[2px] active:translate-y-[2px] active:shadow-none",
      danger:
        "bg-[#D92626] text-white border-2 border-black shadow-brutal-sm hover:bg-black hover:text-white active:translate-x-[2px] active:translate-y-[2px] active:shadow-none",
      ghost:
        "bg-transparent text-black border-2 border-transparent hover:border-black hover:bg-[#E5E5E0]",
    };

    const sizes: Record<NonNullable<ButtonProps["size"]>, string> = {
      sm: "px-3 py-1.5 text-xs font-bold uppercase tracking-wider",
      md: "px-4 py-2.5 text-xs font-bold uppercase tracking-wider",
      lg: "px-6 py-3.5 text-sm font-bold uppercase tracking-widest",
    };

    return (
      <button
        ref={ref}
        className={cn(
          "inline-flex items-center justify-center rounded-none font-mono transition-transform disabled:opacity-50 disabled:pointer-events-none select-none",
          variants[variant],
          sizes[size],
          className
        )}
        {...props}
      />
    );
  }
);
Button.displayName = "Button";

export function Card({
  className,
  ...props
}: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn(
        "rounded-none border-2 border-black bg-white text-black shadow-brutal",
        className
      )}
      {...props}
    />
  );
}

export function CardHeader({
  className,
  ...props
}: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn(
        "border-b-2 border-black bg-[#F4F4F0] px-4 py-3 flex items-center justify-between",
        className
      )}
      {...props}
    />
  );
}

export function CardTitle({
  className,
  ...props
}: React.HTMLAttributes<HTMLHeadingElement>) {
  return (
    <h3
      className={cn(
        "font-mono text-xs font-bold uppercase tracking-widest text-black",
        className
      )}
      {...props}
    />
  );
}

export function CardContent({
  className,
  ...props
}: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("p-4", className)} {...props} />;
}

export const Input = React.forwardRef<
  HTMLInputElement,
  React.InputHTMLAttributes<HTMLInputElement>
>(({ className, ...props }, ref) => (
  <input
    ref={ref}
    className={cn(
      "w-full rounded-none border-2 border-black bg-white px-3 py-2 font-mono text-sm text-black placeholder:text-neutral-500 focus:outline-none focus:bg-[#F4F4F0]",
      className
    )}
    {...props}
  />
));
Input.displayName = "Input";

export function Label({
  className,
  ...props
}: React.LabelHTMLAttributes<HTMLLabelElement>) {
  return (
    <label
      className={cn(
        "block font-mono text-xs font-bold uppercase tracking-wider text-black mb-1.5",
        className
      )}
      {...props}
    />
  );
}

export function Badge({
  className,
  variant = "default",
  ...props
}: React.HTMLAttributes<HTMLSpanElement> & {
  variant?: "default" | "outline" | "inverse" | "alert" | "ok";
}) {
  const styles = {
    default: "bg-[#E5E5E0] text-black border-black",
    outline: "bg-white text-black border-black",
    inverse: "bg-black text-white border-black",
    alert: "bg-[#D92626] text-white border-black",
    ok: "bg-[#15803D] text-white border-black",
  };
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-none border-2 px-2 py-0.5 font-mono text-[11px] font-bold uppercase tracking-wider",
        styles[variant],
        className
      )}
      {...props}
    />
  );
}

export function Progress({
  value,
  max = 100,
  className,
}: {
  value: number;
  max?: number;
  className?: string;
}) {
  const pct = Math.min(100, Math.max(0, (value / Math.max(1, max)) * 100));
  return (
    <div
      className={cn(
        "h-5 w-full rounded-none border-2 border-black bg-white p-0.5",
        className
      )}
    >
      <div
        className="h-full bg-black transition-all duration-150"
        style={{ width: `${pct}%` }}
      />
    </div>
  );
}

export function Separator({ className }: { className?: string }) {
  return <hr className={cn("border-t-2 border-black my-4", className)} />;
}
