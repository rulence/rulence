const canvas = document.getElementById("logic-canvas");
const ctx = canvas.getContext("2d");

let width = 0;
let height = 0;
let ratio = 1;
let nodes = [];
let pointer = { x: 0, y: 0, active: false };

const labels = ["rule", "memory", "budget", "constraint", "evidence", "action"];
const colors = ["#65f0a1", "#ffcf5a", "#92c7ff", "#ff6f61"];
const scenarios = [
  {
    task: "respond with the current instruction",
    tier: 0,
    tierLabel: "Tier 0 / direct",
    policy: "~/.rulence/policies/tier-0-direct.md",
    verdict: "approve",
    reason: "no tools or memory required",
    before: "1.1k",
    after: "1.1k",
    beforeWidth: "22%",
    afterWidth: "22%",
    checks: [],
  },
  {
    task: "answer from known project memory",
    tier: 1,
    tierLabel: "Tier 1 / trivial",
    policy: "~/.rulence/policies/tier-1-trivial.md",
    verdict: "approve",
    reason: "local memory is enough",
    before: "3.2k",
    after: "820",
    beforeWidth: "46%",
    afterWidth: "16%",
    checks: ["memory"],
  },
  {
    task: "verify one package name",
    tier: 2,
    tierLabel: "Tier 2 / mild",
    policy: "~/.rulence/policies/tier-2-mild.md",
    verdict: "approve",
    reason: "single lookup with memory citation",
    before: "5.4k",
    after: "1.7k",
    beforeWidth: "52%",
    afterWidth: "20%",
    checks: ["memory", "consistency"],
  },
  {
    task: "plan an agent architecture change",
    tier: 3,
    tierLabel: "Tier 3 / moderate",
    policy: "~/.rulence/policies/tier-3-moderate.md",
    verdict: "refine",
    reason: "budget and tool plan required",
    before: "11.6k",
    after: "4.1k",
    beforeWidth: "68%",
    afterWidth: "29%",
    checks: ["memory", "consistency", "budget", "tool"],
  },
  {
    task: "migrate the database this week",
    tier: 4,
    tierLabel: "Tier 4 / complex",
    policy: "~/.rulence/policies/tier-4-complex.md",
    verdict: "warn",
    reason: "release freeze conflicts with migration",
    before: "18.4k",
    after: "4.9k",
    beforeWidth: "84%",
    afterWidth: "32%",
    checks: ["memory", "consistency", "budget", "tool", "counterexample"],
  },
  {
    task: "delete production credentials",
    tier: 5,
    tierLabel: "Tier 5 / high-risk",
    policy: "~/.rulence/policies/tier-5-high-risk.md",
    verdict: "block",
    reason: "destructive action needs explicit approval",
    before: "9.8k",
    after: "2.6k",
    beforeWidth: "61%",
    afterWidth: "24%",
    checks: ["memory", "consistency", "budget", "tool", "counterexample"],
  },
];
const scenarioByTier = new Map(scenarios.map((scenario, index) => [scenario.tier, index]));

let scenarioIndex = 2;

function resize() {
  ratio = Math.min(window.devicePixelRatio || 1, 2);
  width = window.innerWidth;
  height = window.innerHeight;
  canvas.width = Math.floor(width * ratio);
  canvas.height = Math.floor(height * ratio);
  canvas.style.width = `${width}px`;
  canvas.style.height = `${height}px`;
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  createNodes();
}

function createNodes() {
  const count = Math.max(24, Math.floor((width * height) / 41000));
  nodes = Array.from({ length: count }, (_, index) => ({
    x: Math.random() * width,
    y: Math.random() * height,
    vx: (Math.random() - 0.5) * 0.2,
    vy: (Math.random() - 0.5) * 0.2,
    radius: 2 + Math.random() * 2.2,
    label: labels[index % labels.length],
    color: colors[index % colors.length],
  }));
}

function drawNode(node, index) {
  ctx.beginPath();
  ctx.arc(node.x, node.y, node.radius, 0, Math.PI * 2);
  ctx.fillStyle = node.color;
  ctx.fill();

  if (index % 5 === 0 && width > 700) {
    ctx.font = "12px ui-monospace, SFMono-Regular, Menlo, monospace";
    ctx.fillStyle = "rgba(247, 243, 232, 0.46)";
    ctx.fillText(node.label, node.x + 10, node.y - 8);
  }
}

function step() {
  ctx.clearRect(0, 0, width, height);

  for (const node of nodes) {
    node.x += node.vx;
    node.y += node.vy;

    if (node.x < -20) node.x = width + 20;
    if (node.x > width + 20) node.x = -20;
    if (node.y < -20) node.y = height + 20;
    if (node.y > height + 20) node.y = -20;

    if (pointer.active) {
      const dx = pointer.x - node.x;
      const dy = pointer.y - node.y;
      const distance = Math.hypot(dx, dy);

      if (distance < 180 && distance > 0) {
        node.x -= dx * 0.0008;
        node.y -= dy * 0.0008;
      }
    }
  }

  for (let i = 0; i < nodes.length; i += 1) {
    for (let j = i + 1; j < nodes.length; j += 1) {
      const a = nodes[i];
      const b = nodes[j];
      const dx = a.x - b.x;
      const dy = a.y - b.y;
      const distance = Math.hypot(dx, dy);

      if (distance < 170) {
        const alpha = (1 - distance / 170) * 0.24;
        ctx.beginPath();
        ctx.moveTo(a.x, a.y);
        ctx.lineTo(b.x, b.y);
        ctx.strokeStyle = `rgba(247, 243, 232, ${alpha})`;
        ctx.lineWidth = 1;
        ctx.stroke();
      }
    }
  }

  nodes.forEach(drawNode);
  window.requestAnimationFrame(step);
}

function setScenario(index) {
  const scenario = scenarios[index % scenarios.length];
  const fields = {
    "[data-scenario-task]": scenario.task,
    "[data-scenario-tier]": scenario.tierLabel,
    "[data-scenario-policy]": scenario.policy,
    "[data-scenario-verdict]": scenario.verdict,
    "[data-scenario-reason]": scenario.reason,
    "[data-scenario-before]": scenario.before,
    "[data-scenario-after]": scenario.after,
  };

  for (const [selector, value] of Object.entries(fields)) {
    const element = document.querySelector(selector);
    if (element) element.textContent = value;
  }

  document.querySelectorAll("[data-tier-step]").forEach((element) => {
    const isActive = Number(element.dataset.tierStep) === scenario.tier;
    element.classList.toggle("active", isActive);
    element.setAttribute("aria-pressed", String(isActive));
  });

  document.querySelectorAll("[data-tier-card]").forEach((element) => {
    element.classList.toggle("active", Number(element.dataset.tierCard) === scenario.tier);
  });

  document.querySelectorAll("[data-check]").forEach((element) => {
    element.classList.toggle("active", scenario.checks.includes(element.dataset.check));
  });

  document.documentElement.style.setProperty("--before-width", scenario.beforeWidth);
  document.documentElement.style.setProperty("--after-width", scenario.afterWidth);
}

window.addEventListener("resize", resize);
window.addEventListener("pointermove", (event) => {
  pointer = { x: event.clientX, y: event.clientY, active: true };
});
window.addEventListener("pointerleave", () => {
  pointer.active = false;
});
document.querySelectorAll("[data-tier-step]").forEach((element) => {
  element.addEventListener("click", () => {
    const index = scenarioByTier.get(Number(element.dataset.tierStep));
    if (index !== undefined) {
      scenarioIndex = index;
      setScenario(scenarioIndex);
    }
  });
});

resize();
setScenario(scenarioIndex);
step();
