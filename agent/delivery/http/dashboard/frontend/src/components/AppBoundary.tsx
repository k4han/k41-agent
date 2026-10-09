import { ErrorBoundary, Suspense, type ParentProps } from "solid-js";

import { ErrorPanel, LoadingPanel } from "@/components/State";

export function AppBoundary(props: ParentProps) {
  return (
    <ErrorBoundary fallback={(error, reset) => (
      <div class="content" role="alert">
        <ErrorPanel
          message={error instanceof Error ? error.message : "The page could not be loaded."}
          onRetry={reset}
        />
        <button class="btn" type="button" onClick={() => window.location.reload()}>
          Reload page
        </button>
      </div>
    )}>
      <Suspense fallback={<div class="content" role="status" aria-live="polite"><LoadingPanel /></div>}>
        {props.children}
      </Suspense>
    </ErrorBoundary>
  );
}
