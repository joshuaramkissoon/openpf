import { RichMarkdown } from './RichMarkdown'

interface Props {
  markdown: string | null
}

export function AgentBrief({ markdown }: Props) {
  const text = markdown?.trim() || 'Run the agent to generate a fresh portfolio brief.'
  return (
    <section className="glass-card brief-card">
      <div className="section-heading-row">
        <h2>Agent Brief</h2>
        <span className="hint">Quant-led insights and actions</span>
      </div>
      <div className="brief-markdown">
        <RichMarkdown markdown={text} />
      </div>
    </section>
  )
}
