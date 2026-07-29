export default function ValidityBadge({ valid, issues }) {
  if (valid) {
    return <span className="badge badge-success">✓ Looks valid</span>;
  }
  const title = issues && issues.length ? issues.join("\n") : "Structural check failed";
  return (
    <span className="badge badge-warning" title={title}>
      ⚠ {issues?.length || 1} issue{issues?.length === 1 ? "" : "s"}
    </span>
  );
}
