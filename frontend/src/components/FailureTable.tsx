import type { FailureReport } from "../types/generated";
import { failureKindLabel, observation, reproductionLabel } from "../services/failure-view";

interface Props {
  failures: FailureReport[];
  selectedId: string | null;
  comparisonIds: string[];
  onSelect: (id: string) => void;
  onCompare: (failure: FailureReport) => void;
}

export function FailureTable({ failures, selectedId, comparisonIds, onSelect, onCompare }: Props) {
  return <div className="table-scroll failure-table"><table className="data-table">
    <caption className="sr-only">Recorded failures. Open a report to inspect execution mode and evidence.</caption>
    <thead><tr><th scope="col">Report / scenario</th><th scope="col">Kind</th><th scope="col">Severity</th><th scope="col">Reproduction</th><th scope="col">Compare</th></tr></thead>
    <tbody>{failures.map(failure => <tr key={failure.id} aria-selected={selectedId === failure.id}>
      <th scope="row"><button type="button" className="text-button" onClick={() => onSelect(failure.id)} aria-label={`Open failure ${failure.id}`}>{failure.scenario.name}</button><small>{failure.id}</small></th>
      <td>{failureKindLabel(failure.kind)}</td><td>{observation(failure.severity)}</td><td><span className="status-badge">{reproductionLabel(failure.reproduced)}</span></td>
      <td><label><input type="checkbox" checked={comparisonIds.includes(failure.id)} disabled={comparisonIds.length >= 2 && !comparisonIds.includes(failure.id)} onChange={() => onCompare(failure)} aria-label={`Compare failure ${failure.id}`} /><span className="sr-only">Compare {failure.scenario.name}</span></label></td>
    </tr>)}</tbody>
  </table></div>;
}
