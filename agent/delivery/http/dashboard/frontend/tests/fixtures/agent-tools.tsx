import "@/styles.css";

import { Route, Router, useParams } from "@solidjs/router";
import { render } from "solid-js/web";
import { ToastProvider } from "@/components/Toast";
import { AgentEditPage } from "@/pages/settings/agents/AgentEditPage";

function Editor() {
  const params = useParams();
  return (
    <AgentEditPage
      agentName={params.name === "new" ? undefined : params.name}
    />
  );
}

render(
  () => (
    <Router root={(props) => <ToastProvider>{props.children}</ToastProvider>}>
      <Route path="/__agent-tools/:name" component={Editor} />
      <Route
        path="/settings/agents"
        component={() => <p>Saved agent configuration.</p>}
      />
      <Route
        path="/settings/providers"
        component={() => <p>Create a web connection.</p>}
      />
    </Router>
  ),
  document.getElementById("root")!,
);
