/* eslint-disable react-refresh/only-export-components */
/**
 * /solutions/:persona — persona-targeted landing surfaces.
 *
 * Persona-targeted pages. Only the pages whose claims can be shown are kept (owner decision G13).
 */
import { Link, Navigate, useParams } from 'react-router-dom';
import { ArrowRight, Sparkles, type LucideIcon } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import { APP_URL, PILOT_MAILTO } from '@/config';

export interface SolutionsContent {
  persona: string;
  heroEyebrow: string;
  heroHeadline: string;
  heroSubhead: string;
  wins: { title: string; description: string }[];
}

// Only pages whose every claim maps to something we can show (owner decision G13, 2026-11-17). The Finance, RevOps, Engineering and
// Every-team pages were removed until each of their claims maps to a test. Keep every sentence consistent with the homepage.
export const PERSONA_CONTENT: Record<string, SolutionsContent> = {
  data: {
    persona: 'Data leaders',
    heroEyebrow: 'Compass for Data Teams',
    heroHeadline: 'Definitions your team owns, certified in Git.',
    heroSubhead:
      'Compass answers only from the metric definitions your people certified, and shows the definitions and evidence behind every number.',
    wins: [
      {
        title: 'Certified definitions in Git',
        description: 'Metric definitions are versioned in Git and certified by people on your team. Only certified definitions run.',
      },
      {
        title: 'Owners on every definition',
        description:
          'Each certified metric and dataset names its owner, so the question of who owns what has a recorded answer.',
      },
      {
        title: 'AI drafts, your team certifies',
        description:
          'In internal testing on synthetic data, Compass can draft definitions from a schema. Nothing runs until a person certifies it.',
      },
    ],
  },
  security: {
    persona: 'Security & Compliance',
    heroEyebrow: 'Compass for Security',
    heroHeadline: 'See who asked what, on which data, when, for every query.',
    heroSubhead:
      'Tenant scope, purpose-bound access, a configurable minimum group size, and a tamper-evident, hash-chained evidence trail on every run. Compass is pilot-stage and has not completed a SOC 2 audit.',
    wins: [
      {
        title: 'An evidence trail you can inspect',
        description:
          'Every run records who asked, the purpose, the definitions used, and the policy decisions. Result rows are not stored in the audit record.',
      },
      {
        title: 'Checked before it runs',
        description:
          'Policy is checked before execution, and answer rows go only to the requester, not to reviewers or assistants.',
      },
      {
        title: 'Separation of duties',
        description:
          'Reviewers see the facts of the run before approving, and requesters cannot approve their own runs.',
      },
    ],
  },
};

const PERSONA_TONES: Record<string, { ring: string; bg: string; text: string }> = {
  data: { ring: 'ring-accent/30', bg: 'bg-accent/15', text: 'text-accent' },
  security: { ring: 'ring-destructive/30', bg: 'bg-destructive/15', text: 'text-destructive' },
};

export function SolutionsPage() {
  const { persona = 'data' } = useParams<{ persona: string }>();
  const content = PERSONA_CONTENT[persona];

  if (!content) {
    return <Navigate to="/solutions/data" replace />;  // a removed or unknown page goes to the first remaining one
  }

  const tone = PERSONA_TONES[persona] ?? PERSONA_TONES.data;
  const otherPersonas = Object.entries(PERSONA_CONTENT).filter(([k]) => k !== persona);

  return (
    <div className="flex-1 overflow-auto">
      <div className="max-w-4xl mx-auto px-4 sm:px-6 md:px-8 py-12 md:py-20">
        {/* Eyebrow */}
        <div className={cn('inline-flex items-center gap-2 text-xs uppercase tracking-widest font-medium px-2.5 py-1 rounded-full', tone.bg, tone.text)}>
          <Sparkles className="w-3 h-3" />
          {content.heroEyebrow}
        </div>

        {/* Headline */}
        <h1 className="text-3xl sm:text-4xl md:text-5xl font-semibold tracking-tight leading-[1.05] mt-5 max-w-3xl">
          {content.heroHeadline}
        </h1>
        <p className="text-fg-secondary text-lg leading-relaxed mt-5 max-w-2xl">
          {content.heroSubhead}
        </p>

        {/* CTAs */}
        <div className="mt-8 flex items-center gap-3 flex-wrap">
          <Button asChild>
            <a href={APP_URL}>
              Try Compass <ArrowRight className="w-4 h-4" />
            </a>
          </Button>
          <Button asChild variant="outline">
            <a href={PILOT_MAILTO}>Talk to us about a pilot</a>
          </Button>
        </div>

        {/* Wins grid */}
        <section className="mt-20">
          <div className="text-xs uppercase tracking-widest text-fg-muted mb-6">
            What you get
          </div>
          <ul className="grid grid-cols-1 md:grid-cols-3 gap-3">
            {content.wins.map((w, i) => (
              <li key={i} className="glass rounded-xl p-5 hover:border-border-strong transition">
                <div className={cn('w-8 h-8 rounded-md flex items-center justify-center mb-3 ring-1', tone.bg, tone.text, tone.ring)}>
                  <Sparkles className="w-4 h-4" />
                </div>
                <h3 className="font-semibold text-base mb-1.5">{w.title}</h3>
                <p className="text-sm text-fg-secondary leading-relaxed">{w.description}</p>
              </li>
            ))}
          </ul>
        </section>

        {/* Other personas */}
        <section className="mt-16">
          <div className="text-xs uppercase tracking-widest text-fg-muted mb-4">
            Compass for other teams
          </div>
          <div className="flex flex-wrap gap-2">
            {otherPersonas.map(([key, p]) => {
              const t = PERSONA_TONES[key] ?? PERSONA_TONES.data;
              return (
                <Link
                  key={key}
                  to={`/solutions/${key}`}
                  className={cn('inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md glass border border-border/60 hover:border-border-strong transition text-sm', t.text)}
                >
                  {p.persona}
                  <ArrowRight className="w-3 h-3 opacity-60" />
                </Link>
              );
            })}
          </div>
        </section>

        <div className="h-12" />
      </div>
    </div>
  );
}

// Re-export the icon type for callers (legacy)
export type { LucideIcon };
