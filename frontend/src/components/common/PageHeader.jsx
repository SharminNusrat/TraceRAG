export function PageHeader({ eyebrow, title, description, actions }) {
  return <header className="page-header">
    <div>
      {eyebrow && <div className="eyebrow"><span />{eyebrow}</div>}
      <h1>{title}</h1>
      {description && <p>{description}</p>}
    </div>
    {actions && <div className="page-actions">{actions}</div>}
  </header>;
}
