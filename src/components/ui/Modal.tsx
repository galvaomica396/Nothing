import type { KeyboardEventHandler, ReactNode } from "react";
import { Button } from "./Button";
import { classNames } from "./classNames";
import { useShellState } from "../../state/shellStore";
import type { ShellModalId } from "../../state/shellStore";

type ModalProps = {
  readonly id: ShellModalId;
  readonly titleId: string;
  readonly title: ReactNode;
  readonly ariaLabel?: string;
  readonly description?: string;
  readonly hidden?: boolean;
  readonly wide?: boolean;
  readonly closeButtonId: string;
  readonly dismissible?: boolean;
  readonly owner?: "legacy" | "react";
  readonly onClose?: () => void;
  readonly onKeyDown?: KeyboardEventHandler<HTMLElement>;
  readonly children: ReactNode;
  readonly footer?: ReactNode;
};

export function Modal({ id, titleId, title, ariaLabel, description, hidden, wide = false, closeButtonId, dismissible = true, owner = "legacy", onClose, onKeyDown, children, footer }: ModalProps) {
  const shell = useShellState();
  const isHidden = hidden ?? !shell.modalVisibility[id];

  return (
    <section
      id={id}
      className={classNames("ux-modal-layer", isHidden && "is-hidden")}
      data-owner={owner}
      aria-label={ariaLabel ?? (typeof title === "string" ? title : undefined)}
      aria-hidden={isHidden ? "true" : "false"}
      data-modal-dismissible={dismissible ? "true" : "false"}
      onKeyDown={onKeyDown}
      onMouseDown={(event) => {
        if (dismissible && event.target === event.currentTarget) onClose?.();
      }}
    >
      <div className={classNames("ux-modal", wide && "ux-modal-wide")} role="dialog" aria-labelledby={titleId} tabIndex={-1}>
        <header className="ux-modal-head">
          <div>
            <h2 id={titleId}>{title}</h2>
            {description ? <p>{description}</p> : null}
          </div>
          <Button id={closeButtonId} type="button" onClick={onClose}>닫기</Button>
        </header>
        {children}
        {footer ? <footer className="ux-modal-actions">{footer}</footer> : null}
      </div>
    </section>
  );
}
