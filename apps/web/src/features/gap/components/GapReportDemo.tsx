"use client";

import { useState } from "react";

import { DEMO_COMPARISON, DEMO_GRAPH, DEMO_MEETING_ID, DEMO_REPORT } from "../fixtures/report-demo";
import { GapReportScreen } from "./GapReportScreen";

/**
 * S20 with a made-up meeting behind it, for reviewing the layout against the
 * design before a database is running locally.
 *
 * **The screen is the real one.** The seam is a `fetch` stub for `/api/gap`
 * installed before the first render, so `GapReportScreen`, its three hooks and
 * their loading and error states all run exactly as they do against the API —
 * a preview that rendered the components directly with props would be a fourth
 * copy of the screen's wiring, and the wiring is most of what there is to
 * review.
 *
 * Nothing in the feature's production path imports this: the only caller is the
 * temporary `/dev-gap` route, which 404s outside development.
 */
export function GapReportDemo() {
  // Installed during render rather than in an effect: the hooks fetch from
  // their own effects, which run after this component's, and a stub installed
  // later would lose the first request. `useState`'s initialiser runs once per
  // mount, including under StrictMode's double render.
  useState(installDemoApi);

  return <GapReportScreen meetingId={DEMO_MEETING_ID} />;
}

function installDemoApi(): true {
  if (typeof window === "undefined") return true;

  const real = window.fetch.bind(window);
  window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    const body = demoBody(url);
    if (body === undefined) return real(input, init);

    // A small delay so the loading state is visible rather than theoretical.
    await new Promise((resolve) => setTimeout(resolve, 150));
    return new Response(JSON.stringify(body), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  };
  return true;
}

function demoBody(url: string): unknown {
  if (url.includes(`/api/gap/reports/${DEMO_MEETING_ID}`)) return DEMO_REPORT;
  if (url.includes(`/api/gap/topics/${DEMO_MEETING_ID}`)) return DEMO_GRAPH;
  if (url.includes(`/api/gap/templates/${DEMO_MEETING_ID}`)) return DEMO_COMPARISON;
  return undefined;
}
