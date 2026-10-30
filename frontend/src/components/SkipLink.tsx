export function SkipLink({ target }: { target: string }) {
  return <a className="skip-link" href={`#${target}`} onClick={event => {
    const destination = document.getElementById(target);
    if (destination) {
      event.preventDefault();
      destination.focus();
      destination.scrollIntoView({ block: "start" });
    }
  }}>Skip to content</a>;
}
