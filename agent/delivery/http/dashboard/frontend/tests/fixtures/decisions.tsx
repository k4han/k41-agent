import "@/styles.css";

import { Route, Router } from "@solidjs/router";
import { render } from "solid-js/web";
import { ToastProvider } from "@/components/Toast";
import { DecisionsPage } from "@/pages/settings/DecisionsPage";

render(() => (
  <Router root={(props) => <ToastProvider>{props.children}</ToastProvider>}>
    <Route path="/__decisions" component={DecisionsPage} />
    <Route path="/settings/providers" component={() => <p>Provider configuration.</p>} />
  </Router>
), document.getElementById("root")!);
