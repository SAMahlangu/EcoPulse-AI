import { useEffect, useRef, type ReactNode } from 'react';
import { X } from 'lucide-react';

/** Native dialog supplies focus containment, Escape and focus restoration. */
export function AccessibleDialog({ titleId, onClose, children }: { titleId: string; onClose(): void; children: ReactNode }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const close = useRef(onClose);
  close.current = onClose;
  useEffect(() => {
    const element = dialog.current;
    const previousFocus = document.activeElement as HTMLElement | null;
    element?.showModal();
    return () => { element?.close(); previousFocus?.focus(); };
  }, []);
  return <dialog ref={dialog} className="incident-dialog accessible-dialog" aria-labelledby={titleId} onCancel={event => { event.preventDefault(); close.current(); }} onClick={event => { if (event.target === event.currentTarget) { const bounds = event.currentTarget.getBoundingClientRect(); if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) close.current(); } }}>
    <button type="button" className="dialog-close" aria-label="Close dialog" onClick={onClose}><X size={18}/></button>{children}
  </dialog>;
}
