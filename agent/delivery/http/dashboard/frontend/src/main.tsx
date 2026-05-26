import { Navigate, Route, Router } from "@solidjs/router";
import { lazy } from "solid-js";
import { render } from "solid-js/web";

import { ToastProvider } from "@/components/Toast";
import "diff2html/bundles/css/diff2html.min.css";
import "@/styles.css";

const ChatHistoryListPage = lazy(() =>
  import("@/pages/ChatHistory").then((module) => ({ default: module.ChatHistoryListPage })),
);
const ChatPage = lazy(() =>
  import("@/pages/Chat").then((module) => ({ default: module.ChatPage })),
);
const LoginPage = lazy(() =>
  import("@/pages/Login").then((module) => ({ default: module.LoginPage })),
);
const OverviewPage = lazy(() =>
  import("@/pages/Overview").then((module) => ({ default: module.OverviewPage })),
);
const RepositoriesPage = lazy(() =>
  import("@/pages/Repositories").then((module) => ({ default: module.RepositoriesPage })),
);
const SchedulerPage = lazy(() =>
  import("@/pages/Scheduler").then((module) => ({ default: module.SchedulerPage })),
);
const SessionsPage = lazy(() =>
  import("@/pages/Sessions").then((module) => ({ default: module.SessionsPage })),
);
const AgentsPage = lazy(() =>
  import("@/pages/settings/AgentsPage").then((module) => ({ default: module.AgentsPage })),
);
const AppearancePage = lazy(() =>
  import("@/pages/settings/AppearancePage").then((module) => ({
    default: module.AppearancePage,
  })),
);
const ChannelsPage = lazy(() =>
  import("@/pages/settings/ChannelsPage").then((module) => ({
    default: module.ChannelsPage,
  })),
);
const ConnectionsPage = lazy(() =>
  import("@/pages/settings/ConnectionsPage").then((module) => ({
    default: module.ConnectionsPage,
  })),
);
const ConfigPage = lazy(() =>
  import("@/pages/settings/ConfigPage").then((module) => ({ default: module.ConfigPage })),
);
const PromptVariablesPage = lazy(() =>
  import("@/pages/settings/PromptVariablesPage").then((module) => ({
    default: module.PromptVariablesPage,
  })),
);
const ProvidersPage = lazy(() =>
  import("@/pages/settings/ProvidersPage").then((module) => ({
    default: module.ProvidersPage,
  })),
);
const SecurityPage = lazy(() =>
  import("@/pages/settings/SecurityPage").then((module) => ({
    default: module.SecurityPage,
  })),
);
const TasksPage = lazy(() =>
  import("@/pages/Tasks").then((module) => ({ default: module.TasksPage })),
);

function NotFoundPage() {
  return <OverviewPage />;
}

render(
  () => (
    <ToastProvider>
      <Router>
        <Route path="/" component={OverviewPage} />
        <Route path="/login" component={LoginPage} />
        <Route path={["/chat", "/c/:threadId"]} component={ChatPage} />
        <Route path="/history" component={ChatHistoryListPage} />
        <Route path="/sessions" component={SessionsPage} />
        <Route path="/repositories" component={RepositoriesPage} />
        <Route path="/tasks" component={TasksPage} />
        <Route path="/scheduler" component={SchedulerPage} />
        <Route path="/settings" component={() => <Navigate href="/settings/config" />} />
        <Route path="/settings/config" component={ConfigPage} />
        <Route path="/settings/providers" component={ProvidersPage} />
        <Route path="/settings/connections" component={ConnectionsPage} />
        <Route path="/settings/channels" component={ChannelsPage} />
        <Route path="/settings/agents" component={AgentsPage} />
        <Route path="/settings/prompt-variables" component={PromptVariablesPage} />
        <Route path="/settings/security" component={SecurityPage} />
        <Route path="/settings/appearance" component={AppearancePage} />
        {/* Legacy redirects */}
        <Route path="/channels" component={() => <Navigate href="/settings/channels" />} />
        <Route path="/agents" component={() => <Navigate href="/settings/agents" />} />
        <Route path="/change-password" component={() => <Navigate href="/settings/security" />} />
        <Route path="*404" component={NotFoundPage} />
      </Router>
    </ToastProvider>
  ),
  document.getElementById("root") as HTMLElement,
);
