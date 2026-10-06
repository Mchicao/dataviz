import React from 'react';

interface RouteErrorBoundaryState {
  error: string;
}

interface RouteErrorBoundaryProps {
  children?: React.ReactNode;
}

/** Keep route chunk failures visible and recoverable instead of leaving a blank root. */
export class RouteErrorBoundary extends React.Component<RouteErrorBoundaryProps, RouteErrorBoundaryState> {
  state: RouteErrorBoundaryState = { error: '' };

  static getDerivedStateFromError(error: unknown): RouteErrorBoundaryState {
    return { error: error instanceof Error ? error.message : 'No se pudo cargar la vista.' };
  }

  render(): React.ReactNode {
    if (!this.state.error) {return this.props.children;}
    return (
      <main className="dv-route-error" role="alert">
        <h1>No se pudo cargar DataVIZ</h1>
        <p>{this.state.error}</p>
        <button onClick={() => window.location.reload()} type="button">Reintentar</button>
      </main>
    );
  }
}
