import { Navigate, Route, Router } from "@solidjs/router";
import { render } from "solid-js/web";

import { ToastProvider } from "@/components/Toast";
import { ChatHistoryDetailPage, ChatHistoryListPage } from "@/pages/ChatHistory";
import { ChatPage } from "@/pages/Chat";
import { LoginPage } from "@/pages/Login";
import { OverviewPage } from "@/pages/Overview";
import { RepositoriesPage } from "@/pages/Repositories";
import { SchedulerPage } from "@/pages/Scheduler";
import { SessionsPage } from "@/pages/Sessions";
import {
  AgentsPage,
  AppearancePage,
  ChannelsPage,
  ConfigPage,
  ProvidersPage,
  SecurityPage,
} from "@/pages/settings";
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
        <Route path="/chat" component={ChatPage} />
        <Route path="/history" component={ChatHistoryListPage} />
        <Route path="/history/:threadId" component={ChatHistoryDetailPage} />
        <Route path="/sessions" component={SessionsPage} />
        <Route path="/repositories" component={RepositoriesPage} />
        <Route path="/tasks" component={TasksPage} />
        <Route path="/scheduler" component={SchedulerPage} />
        <Route path="/settings" component={() => <Navigate href="/settings/config" />} />
        <Route path="/settings/config" component={ConfigPage} />
        <Route path="/settings/providers" component={ProvidersPage} />
        <Route path="/settings/channels" component={ChannelsPage} />
        <Route path="/settings/agents" component={AgentsPage} />
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
