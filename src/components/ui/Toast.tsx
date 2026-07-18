import type { HTMLAttributes, ReactNode } from "react";
import { classNames } from "./classNames";

type ToastTone = "idle" | "working" | "done" | "blocked";

type ToastProps = HTMLAttributes<HTMLDivElement> & {
  readonly children: ReactNode;
  readonly tone?: ToastTone;
};

export function Toast({ children, className, tone = "idle", ...props }: ToastProps) {
  return (
    <div className={classNames("ui-toast", `ui-toast-${tone}`, className)} role="status" {...props}>
      {children}
    </div>
  );
}
