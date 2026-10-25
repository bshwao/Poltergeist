import { createRoot } from "react-dom/client";
import { HostedApp } from "./App";
import "./hosted.css";

createRoot(document.getElementById("root")!).render(<HostedApp />);
