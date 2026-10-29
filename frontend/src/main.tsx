import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { Toaster } from "sonner";
import App from "./App";
import { RoleProvider } from "./lib/role";
import { TooltipProvider } from "./components/ui/primitives";
import "./index.css";

const qc = new QueryClient({ defaultOptions: { queries: { staleTime: 5000, retry: 1, refetchOnWindowFocus: false } } });

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={qc}>
      <RoleProvider>
        <TooltipProvider>
          <BrowserRouter><App /></BrowserRouter>
          <Toaster position="bottom-right" richColors closeButton />
        </TooltipProvider>
      </RoleProvider>
    </QueryClientProvider>
  </React.StrictMode>,
);
