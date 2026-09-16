"use client";

import { useEffect, useState, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import { getAdminKey } from "@/lib/admin";

/**
 * AdminGuard — redirects to /admin/login when this tab's session has no
 * admin key. The key lives in sessionStorage, so closing the tab ends the
 * admin session automatically.
 */
export function AdminGuard({ children }: { children: ReactNode }) {
  const router = useRouter();
  const [checked, setChecked] = useState(false);
  const [authed, setAuthed] = useState(false);

  useEffect(() => {
    if (!getAdminKey()) {
      router.replace("/admin/login");
    } else {
      setAuthed(true);
    }
    setChecked(true);
  }, [router]);

  if (!checked || !authed) return null;
  return <>{children}</>;
}
