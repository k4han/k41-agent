import { Route, Router } from "@solidjs/router";
import { render } from "solid-js/web";

import { ToastProvider } from "@/components/Toast";
import { AgentsPage } from "@/pages/Agents";
import { ChangePasswordPage } from "@/pages/ChangePassword";
import { ChannelsPage } from "@/pages/Channels";
import { ChatPage } from "@/pages/Chat";
import { LoginPage } from "@/pages/Login";
import { OverviewPage } from "@/pages/Overview";
import { SchedulerPage } from "@/pages/Scheduler";
import { SessionsPage } from "@/pages/Sessions";
import { SettingsPage } from "@/pages/Settings";
import { TasksPage } from "@/pages/Tasks";
import "@/styles.css";

function NotFoundPage() {
  return <OverviewPage />;
}

render(
  () => (
    <ToastProvider>
      <Router>
        <Route path="/" component={OverviewPage} />
        <Route path="/login" component={LoginPage} />
        <Route path="/channels" component={ChannelsPage} />
        <Route path="/agents" component={AgentsPage} />
        <Route path="/chat" component={ChatPage} />
        <Route path="/sessions" component={SessionsPage} />
        <Route path="/tasks" component={TasksPage} />
        <Route path="/scheduler" component={SchedulerPage} />
        <Route path="/config" component={() => <SettingsPage mode="config" />} />
        <Route path="/providers" component={() => <SettingsPage mode="providers" />} />
        <Route path="/change-password" component={ChangePasswordPage} />
        <Route path="*404" component={NotFoundPage} />
      </Router>
    </ToastProvider>
  ),
  document.getElementById("root") as HTMLElement,
);

