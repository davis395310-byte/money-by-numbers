"use client";

import { useEffect, useState } from "react";
import {
  AFFILIATE_DISCLOSURE,
  BRAND_NAME,
  RESPONSIBLE_GAMBLING_NOTICE,
} from "@/lib/config";
import { fetchDisclosure } from "@/lib/api";

/**
 * Site footer. The affiliate disclosure is fetched from
 * GET /api/affiliate/disclosure so it stays configurable via the
 * AFFILIATE_DISCLOSURE_TEXT env var; the bundled constant is the fallback
 * when the backend is unreachable. Always visible on every page.
 */
export function Footer() {
  const year = new Date().getFullYear();
  const [disclosure, setDisclosure] = useState<string>(AFFILIATE_DISCLOSURE);

  useEffect(() => {
    let cancelled = false;
    fetchDisclosure().then((res) => {
      if (!cancelled && res && res.disclosure) {
        setDisclosure(res.disclosure);
      }
    });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <footer className="footer">
      <div className="container">
        <div className="footer-brand">{BRAND_NAME}</div>
        <p>{disclosure}</p>
        <div className="rg-note">
          <p>{RESPONSIBLE_GAMBLING_NOTICE}</p>
        </div>
        <p className="footer-copy">
          &copy; {year} {BRAND_NAME}. All rights reserved.
        </p>
      </div>
    </footer>
  );
}
