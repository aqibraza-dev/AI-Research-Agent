import { Link } from "react-router-dom";
import { ArrowRight, BookOpen, Calendar, ChartNoAxesCombined, Check, FileText, Globe, MessageSquare, Shield, Sparkles } from "lucide-react";
import { useApp } from "./context";
import "./landing.css";

const features = [
  { icon: Globe, title: "Start with evidence", text: "Research the live web and explore reports with linked sources you can check.", path: "/research" },
  { icon: BookOpen, title: "Keep every discovery", text: "Find, compare, and revisit your saved reports in one personal research history.", path: "/history" },
  { icon: MessageSquare, title: "Keep the conversation going", text: "Ask follow-up questions, bring a report into chat, and export your answers.", path: "/chat" },
  { icon: Calendar, title: "Make room for curiosity", text: "Schedule recurring research and receive completed reports in your workspace.", path: "/schedules" },
  { icon: ChartNoAxesCombined, title: "Understand your usage", text: "See your token history, recorded costs, and remaining daily allowance.", path: "/analytics" },
  { icon: Shield, title: "Explore model behavior", text: "Run built-in red-team tests or test an API you own, then review the results.", path: "/redteam" },
];

export function LandingPage() {
  const { session } = useApp();
  return (
    <div className="landing">
      <a className="landing-skip" href="#home-content">Skip to content</a>
      <header className="landing-header">
        <Link to="/" className="brand"><span><Sparkles size={20} /></span>AI Research</Link>
        <nav aria-label="Main navigation">
          <a href="#features">Features</a>
          <a href="#how-it-works">How it works</a>
        </nav>
        <div className="landing-actions">
          {session ? <Link className="button primary" to="/research">Open workspace <ArrowRight size={16} /></Link> : <>
            <Link className="landing-signin" to="/login">Sign in</Link>
            <Link className="button primary" to="/signup">Get started <ArrowRight size={16} /></Link>
          </>}
        </div>
      </header>
      <main id="home-content">
        <section className="landing-hero" aria-labelledby="hero-title">
          <div className="landing-intro">
            <span className="landing-kicker"><span /> A little curiosity. A clearer picture.</span>
            <h1 id="hero-title">Big questions.<br /><em>Better understanding.</em></h1>
            <p>Turn your curiosity into research you can build on. Discover sources, connect ideas, and keep every insight in one thoughtful workspace.</p>
            <div className="landing-hero-actions">
              <Link className="button primary" to={session ? "/research" : "/signup"}>{session ? "Go to your workspace" : "Create your workspace"}<ArrowRight size={18} /></Link>
              <a className="landing-text-link" href="#how-it-works">See how it works <span aria-hidden="true">↘</span></a>
            </div>
            <p className="landing-note"><Shield size={14} /> Sign in to research, chat, schedule, and save your work.</p>
          </div>
          <div className="landing-illustration" aria-label="Illustration of the research workflow">
            <div className="landing-orbit" />
            <div className="landing-paper">
              <div className="landing-paper-top"><span><FileText size={17} /> FROM QUESTION TO CLARITY</span><Sparkles size={18} /></div>
              <span className="landing-paper-label">YOUR NEXT DISCOVERY</span>
              <h2>Follow the question.<br />Find the evidence.</h2>
              <div className="landing-paper-lines" aria-hidden="true"><i /><i /><i /></div>
              <div className="landing-source"><Globe size={18} /><div><strong>Explore real sources</strong><span>Keep the context behind each insight.</span></div></div>
              <div className="landing-source"><MessageSquare size={18} /><div><strong>Ask the next question</strong><span>Build on what you have learned.</span></div></div>
              <div className="landing-paper-bottom"><span><Check size={14} /> Research</span><span><Check size={14} /> Review</span><span><Check size={14} /> Revisit</span></div>
            </div>
            <div className="landing-export"><FileText size={20} /><div><strong>Take your ideas with you</strong><span>PDF & Markdown exports</span></div></div>
          </div>
        </section>
        <div className="landing-principles"><span>Live web research</span><span>Sources you can revisit</span><span>Your personal workspace</span><span>Ideas worth keeping</span></div>
        <section className="landing-section" id="features" aria-labelledby="features-title">
          <div className="landing-section-heading"><span className="eyebrow">A PLACE FOR YOUR THINKING</span><h2 id="features-title">From the first question<br />to the next great idea.</h2><p>Everything you need to explore a topic, understand the details, and come back to what matters.</p></div>
          <div className="landing-features">{features.map(({ icon: Icon, title, text, path }) => <article key={path}>
            <span className="landing-feature-icon"><Icon size={22} /></span><h3>{title}</h3><p>{text}</p><Link to={path} aria-label={`Explore this feature: ${title}`}>Explore this feature <ArrowRight size={15} /><span className="landing-sr-only">: {title}</span></Link>
          </article>)}</div>
        </section>
        <section className="landing-how landing-section" id="how-it-works" aria-labelledby="how-title">
          <div className="landing-section-heading"><span className="eyebrow">A SIMPLE WAY TO GO DEEPER</span><h2 id="how-title">Bring a question.<br />Leave with perspective.</h2></div>
          <ol>{[
            ["Make yourself at home", "Create an account and sign in to your research workspace."],
            ["Choose what to explore", "Ask a research question. Follow the search, draft, and review as your report takes shape."],
            ["Make the insight yours", "Check the sources, continue in chat, export a report, or schedule your next exploration."],
          ].map(([title, text], i) => <li key={title}><span>0{i + 1}</span><div><h3>{title}</h3><p>{text}</p></div></li>)}</ol>
        </section>
        <section className="landing-finish"><Sparkles size={27} /><h2>Your next discovery starts here.</h2><p>A question is all you need to begin. Your workspace keeps the rest together.</p><Link className="button primary" to={session ? "/research" : "/signup"}>{session ? "Open your workspace" : "Get started with AI Research"}<ArrowRight size={17} /></Link><small>Research uses available provider capacity. Scheduled delivery is best effort.</small></section>
      </main>
      <footer className="landing-footer"><Link to="/" className="brand"><Sparkles size={19} />AI Research</Link><p>Evidence first. Clearer thinking.</p><Link to={session ? "/research" : "/login"}>{session ? "Workspace" : "Sign in to your workspace"} <ArrowRight size={14} /></Link></footer>
    </div>
  );
}
