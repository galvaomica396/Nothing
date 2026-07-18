import type { ButtonHTMLAttributes, ReactNode } from "react";
import { classNames } from "./classNames";

type ButtonVariant = "primary" | "secondary" | "ghost" | "nav";
type ButtonSize = "sm" | "md" | "lg";

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  readonly children: ReactNode;
  readonly variant?: ButtonVariant;
  readonly size?: ButtonSize;
  readonly isActive?: boolean;
};

export function Button({ children, className, variant = "secondary", size = "md", isActive = false, ...props }: ButtonProps) {
  return (
    <button
      className={classNames("ui-button", `ui-button-${variant}`, `ui-button-${size}`, isActive && "is-active", className)}
      {...props}
    >
      {children}
    </button>
  );
}
