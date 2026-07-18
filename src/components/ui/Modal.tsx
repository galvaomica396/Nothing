import type { ReactNode } from "react";
import { Button } from "./Button";
import { classNames } from "./classNames";

type ModalProps = {
  readonly id: string;
  readonly titleId: string;
  readonly title: string;
  readonly description?: string;
  readonly hidden?: boolean;
  readonly wide?: boolean;
  readonly closeButtonId: string;
  readonly children: ReactNode;
  readonly footer?: ReactNode;
};

export function Modal({ id, titleId, title, description, hidden = true, wide = false, closeButtonId, children, footer }: ModalProps) {
  return (
    <section id={id} className="ux-modal-layer is-hidden" aria-label={title} aria-hidden={hidden ? "true" : "false"}>
      <div className={classNames("ux-modal", wide && "ux-modal-wide")} role="dialog" aria-modal="true" aria-labelledby={titleId}>
        <header className="ux-modal-head">
          <div>
            <h2 id={titleId}>{title}</h2>
            {description ? <p>{description}</p> : null}
          </div>
          <Button id={closeButtonId} type="button">닫기</Button>
        </header>
        {children}
        {footer ? <footer className="ux-modal-actions">{footer}</footer> : null}
      </div>
    </section>
  );
}
