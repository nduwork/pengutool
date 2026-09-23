import * as vscode from 'vscode';
import { ServeClient } from './serveClient';
import { SessionsProvider } from './sessionsTree';
import { HintsProvider } from './hintsView';
import { TerminalManager } from './terminals';
import { MapPanel } from './mapPanel';
import { LogPanel } from './logPanel';
import { registerCommands } from './commands';
import { DefaultLayout } from './defaultLayout';
import { SessionsView } from './sessionsView';
import { bothHarnessesContext } from './harness';

export function activate(context: vscode.ExtensionContext): void {
  const output = vscode.window.createOutputChannel('PenguPool');
  const provider = new SessionsProvider();
  const terminals = new TerminalManager(context);
  const client = new ServeClient(output);
  let setupNoticeShown = false;
  const tree = new SessionsView(provider);
  const piTree = new SessionsView(provider, 'pi');   // shown only while both harnesses run
  const webviewOptions = { webviewOptions: { retainContextWhenHidden: true } };
  const sessions = vscode.window.registerWebviewViewProvider('pengupoolSessions', tree, webviewOptions);
  const piSessions = vscode.window.registerWebviewViewProvider('pengupoolPiSessions', piTree, webviewOptions);
  const syncBoth = bothHarnessesContext();
  const hints = vscode.window.registerTreeDataProvider('pengupoolHints', new HintsProvider());
  const layout = new DefaultLayout(context, tree, terminals);

  client.onSnapshot((snap) => {
    provider.update(snap);
    syncBoth(snap.roots);
    tree.update(snap);
    piTree.update(snap);
    terminals.reconcile(snap.roots);        // bind freshly launched terminals to their sessionId
    layout.update(snap);
    MapPanel.showIfOpen()?.update(snap);
    LogPanel.showIfOpen()?.update(snap);
  });
  context.subscriptions.push(client.onSpawnError((err) => {
    if (setupNoticeShown) { return; }
    setupNoticeShown = true;
    void vscode.window.showErrorMessage(
      `PenguPool could not start its CLI (${err.message}). Install the backend or set pengupool.command.`,
      'Setup guide', 'Open setting',
    ).then((choice) => {
      if (choice === 'Setup guide') {
        void vscode.env.openExternal(vscode.Uri.parse('https://github.com/nduwork/pengutool/blob/main/extension/README.md#setup'));
      } else if (choice === 'Open setting') {
        void vscode.commands.executeCommand('workbench.action.openSettings', 'pengupool.command');
      }
    });
  }));

  registerCommands(context, { provider, terminals, tree });

  context.subscriptions.push(
    output, tree, piTree, sessions, piSessions, hints, terminals, client, layout,
    vscode.commands.registerCommand('pengupool.showMap', () => MapPanel.toggle(context, client.lastSnapshot)),
    vscode.commands.registerCommand('pengupool.showLog', () => LogPanel.toggle(client.lastSnapshot)),
    vscode.commands.registerCommand('pengupool.refresh', () => client.restart()),
  );

  client.start();
}

export function deactivate(): void { /* subscriptions dispose the client + terminals */ }
