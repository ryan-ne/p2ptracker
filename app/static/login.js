"use strict";

const form = document.getElementById("loginForm");
const err = document.getElementById("err");
const btn = document.getElementById("submitBtn");
const pw = document.getElementById("password");

// Only allow same-site relative redirects (no "//evil.com" or absolute URLs).
function nextUrl() {
  const n = new URLSearchParams(location.search).get("next") || "/";
  return n.startsWith("/") && !n.startsWith("//") && !n.startsWith("/\\") ? n : "/";
}

function showError(msg) {
  err.textContent = msg;
  err.hidden = !msg;
}

document.getElementById("pwToggle").addEventListener("click", (e) => {
  const show = pw.type === "password";
  pw.type = show ? "text" : "password";
  e.currentTarget.textContent = show ? "Hide" : "Show";
  e.currentTarget.setAttribute("aria-label", show ? "Hide password" : "Show password");
  pw.focus();
});

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const username = form.username.value.trim();
  const password = pw.value;
  if (!username || !password) { showError("Enter your username and password."); return; }
  showError("");
  btn.disabled = true;
  btn.textContent = "Signing in…";
  try {
    const r = await fetch("/api/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    });
    if (r.ok) { location.replace(nextUrl()); return; }
    let msg = "Sign-in failed.";
    try { msg = (await r.json()).detail || msg; } catch {}
    showError(msg);
    pw.select();
  } catch {
    showError("Can't reach the server. Check your connection.");
  }
  btn.disabled = false;
  btn.textContent = "Sign in";
});
