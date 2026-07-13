const form = document.querySelector("#loginForm");
const usernameInput = document.querySelector("#username");
const passwordInput = document.querySelector("#password");
const errorBox = document.querySelector("#loginError");

async function checkSession() {
  const response = await fetch("/api/auth/me");
  if (!response.ok) return;
  const payload = await response.json();
  if (payload.authenticated) {
    window.location.href = "/";
  }
}

function setError(message) {
  errorBox.textContent = message || "";
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  setError("");
  const button = form.querySelector("button");
  button.disabled = true;
  try {
    const response = await fetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        username: usernameInput.value.trim(),
        password: passwordInput.value,
      }),
    });
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      throw new Error(payload.error || "登录失败");
    }
    window.location.href = "/";
  } catch (error) {
    setError(error.message);
    button.disabled = false;
  }
});

checkSession().catch(() => {});
