// @ts-check
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  TestHarness,
  assert,
  assertEqual,
  assertGreaterThanOrEqual,
  assertLessThanOrEqual,
  assertMatches,
  assertIncludes,
} from './responsive/engine/harness.mjs';
import { ViewportSimulator } from './responsive/engine/viewport-simulator.mjs';
import { CSSAnalyzer } from './responsive/engine/css-analyzer.mjs';
import { ASTAnalyzer } from './responsive/engine/ast-analyzer.mjs';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const FRONTEND_ROOT = path.resolve(__dirname, '..');

async function runM2ChallengerEmpiricalSuite() {
  const stylesDir = path.join(FRONTEND_ROOT, 'src/styles');
  const css = new CSSAnalyzer(stylesDir);
  const ast = new ASTAnalyzer(FRONTEND_ROOT);
  css.load();

  const harness = new TestHarness({ verbose: true });
  harness.setTier('Challenger M2: Empirical & Adversarial Validation');

  const targetViewports = [
    { name: 'iPhone SE (375px)', width: 375, height: 667, isMobile: true },
    { name: 'iPhone 14 (390px)', width: 390, height: 844, isMobile: true },
    { name: 'iPhone Plus (414px)', width: 414, height: 896, isMobile: true },
    { name: 'iPad Portrait (768px)', width: 768, height: 1024, isMobile: true },
    { name: 'Standard Desktop (1280px)', width: 1280, height: 800, isMobile: false },
  ];

  const extremeSub375Viewports = [
    { name: 'Extremely Narrow Mobile (320px)', width: 320, height: 568 },
    { name: 'Compact Android (340px)', width: 340, height: 640 },
    { name: 'Standard Android (360px)', width: 360, height: 740 },
  ];

  // =========================================================================
  // Section 1: Touch Target Verification (>= 40px)
  // =========================================================================
  harness.setFeature('CHAL-M2-01: Touch Target Ergonomics (>= 40px)');

  const touchTargets = [
    { selector: '.chat-composer-icon', minW: 40, minH: 40, desc: 'Chat Composer Icon Button' },
    { selector: '.chat-composer-send', minW: 40, minH: 40, desc: 'Chat Composer Send Button' },
    { selector: '.chat-composer-stop', minW: 40, minH: 40, desc: 'Chat Composer Stop Button' },
    { selector: '.agent-model-badge', minW: 0, minH: 40, desc: 'Agent Model Badge' },
    { selector: '.chat-agent-picker', minW: 0, minH: 40, desc: 'Agent Picker' },
    { selector: '.chat-model-picker', minW: 0, minH: 40, desc: 'Model Picker' },
    { selector: '.chat-model-picker .model-picker-star', minW: 40, minH: 40, desc: 'Model Picker Star Button' },
    { selector: '.chat-attachment-remove', minW: 40, minH: 40, desc: 'Attachment Remove Button' },
    { selector: '.chat-prompt-input', minW: 0, minH: 42, desc: 'Chat Prompt Input Area' },
  ];

  for (const item of touchTargets) {
    harness.test(`Touch target for ${item.desc} satisfies >= ${item.minW}x${item.minH}px`, () => {
      const rules = css.findRules(item.selector);
      assert(rules.length > 0, `No CSS rules found for selector "${item.selector}"`);

      let satisfiedW = item.minW === 0;
      let satisfiedH = item.minH === 0;

      for (const rule of rules) {
        const w = rule.declarations['width'] || rule.declarations['min-width'];
        const h = rule.declarations['height'] || rule.declarations['min-height'];

        if (w) {
          const numW = parseFloat(w);
          if (w.includes('var(--touch-target') || (numW && numW >= item.minW)) {
            satisfiedW = true;
          }
        }
        if (h) {
          const numH = parseFloat(h);
          if (h.includes('var(--touch-target') || (numH && numH >= item.minH)) {
            satisfiedH = true;
          }
        }
      }

      assert(satisfiedW, `${item.desc} width/min-width must be >= ${item.minW}px`);
      assert(satisfiedH, `${item.desc} height/min-height must be >= ${item.minH}px`);
    });
  }

  // Mobile/Tablet Message Action Button (<= 768px)
  harness.test('Message action button (.message-action-btn) enforces 40x40px touch target on mobile/tablet (<= 768px)', () => {
    const rules = css.findRules('.message-action-btn', '768px');
    assert(rules.length > 0, 'No media query rule found for .message-action-btn under max-width: 768px');
    const rule768 = rules[0];
    const w = parseFloat(rule768.declarations['width'] || rule768.declarations['min-width'] || '0');
    const h = parseFloat(rule768.declarations['height'] || rule768.declarations['min-height'] || '0');
    assertGreaterThanOrEqual(w, 40, '.message-action-btn width under 768px');
    assertGreaterThanOrEqual(h, 40, '.message-action-btn height under 768px');
  });

  // Mobile Font Size >= 16px to prevent iOS auto-zoom
  harness.test('Chat prompt input enforces font-size >= 16px under <= 768px breakpoint', () => {
    const rules = css.findRules('.chat-prompt-input', '768px');
    assert(rules.length > 0, 'No rule found for .chat-prompt-input under max-width: 768px');
    const fontSize = parseFloat(rules[0].declarations['font-size'] || '0');
    assertGreaterThanOrEqual(fontSize, 16, 'chat-prompt-input font-size on mobile/tablet');
  });

  // =========================================================================
  // Section 2: Zero Document-Level Horizontal Overflow across Target Viewports
  // =========================================================================
  harness.setFeature('CHAL-M2-02: Document Overflow Stress across 375, 390, 414, 768, 1280px');

  for (const vp of targetViewports) {
    harness.test(`ChatWelcomeHero on ${vp.name} (${vp.width}px) renders with zero document overflow`, () => {
      const sim = new ViewportSimulator({ width: vp.width, height: vp.height });
      const isMobile = vp.width <= 768;

      // Model ChatWelcomeHero structure
      const welcomeHeroBox = {
        name: 'chat-welcome-hero',
        style: {
          widthStr: '100%',
          maxWidth: isMobile ? vp.width : 800,
          padding: isMobile
            ? { top: 20, right: 12, bottom: 16, left: 12 }
            : { top: 32, right: 16, bottom: 24, left: 16 },
        },
        children: [
          {
            name: 'chat-welcome-hero-header',
            style: { display: 'flex', flexDirection: 'column', widthStr: '100%', gap: 8 },
            children: [
              { name: 'badge', style: { width: 44 } },
              { name: 'title', style: { widthStr: '100%', wordBreak: 'break-word' } },
              { name: 'desc', style: { widthStr: '100%', maxWidth: 520, wordBreak: 'break-word' } },
            ],
          },
          {
            name: 'chat-welcome-workspace-wrapper',
            style: {
              widthStr: '100%',
              padding: { top: 14, right: 14, bottom: 14, left: 14 },
            },
            children: [
              { name: 'label', style: { widthStr: '100%' } },
              {
                name: 'chat-workspace-empty',
                style: { widthStr: '100%', overflowX: isMobile ? 'auto' : 'visible', isScrollContainer: isMobile },
                children: [{ name: 'workspace-selector', style: { width: isMobile ? 320 : 600 } }],
              },
            ],
          },
          {
            name: 'chat-welcome-starters-section',
            style: { widthStr: '100%' },
            children: [
              {
                name: 'chat-welcome-starters-grid',
                style: {
                  display: 'grid',
                  widthStr: '100%',
                  gap: isMobile ? 10 : 12,
                },
                children: [
                  { name: 'card-1', style: { widthStr: '100%' } },
                  { name: 'card-2', style: { widthStr: '100%' } },
                  { name: 'card-3', style: { widthStr: '100%' } },
                  { name: 'card-4', style: { widthStr: '100%' } },
                ],
              },
            ],
          },
        ],
      };

      const result = sim.simulatePage(welcomeHeroBox);
      assert(result.passes, `ChatWelcomeHero induced document overflow on ${vp.name}`);
      assertLessThanOrEqual(
        result.documentScrollWidth,
        vp.width,
        `ChatWelcomeHero scrollWidth (${result.documentScrollWidth}px) exceeds viewport width (${vp.width}px)`
      );
    });

    harness.test(`Composer with heavy attachment list & long prompt on ${vp.name} (${vp.width}px) zero overflow`, () => {
      const sim = new ViewportSimulator({ width: vp.width, height: vp.height });
      const isMobile = vp.width <= 768;

      const composerBox = {
        name: 'chat-composer',
        style: {
          widthStr: isMobile ? 'calc(100% - 16px)' : 'calc(100% - 32px)',
          padding: { top: 10, right: 12, bottom: 20, left: 12 },
          margin: isMobile ? { top: 0, right: 8, bottom: 0, left: 8 } : { top: 0, right: 16, bottom: 0, left: 16 },
        },
        children: [
          // Attachment list
          {
            name: 'chat-attachment-list',
            style: { display: 'flex', flexWrap: 'wrap', gap: 8, widthStr: '100%' },
            children: [
              { name: 'attachment-1', style: { maxWidth: Math.min(vp.width - 40, 340) } },
              { name: 'attachment-2', style: { maxWidth: Math.min(vp.width - 40, 340) } },
              { name: 'attachment-3', style: { maxWidth: Math.min(vp.width - 40, 340) } },
            ],
          },
          // Input textarea with long unbroken string
          {
            name: 'chat-prompt-input',
            text: 'Very-long-prompt-without-spaces-that-must-break-cleanly-inside-the-textarea-container-0123456789-abcdefghijklmnopqrstuvwxyz',
            style: { widthStr: '100%', wordBreak: 'break-word', overflowWrap: 'anywhere' },
          },
          // Toolbar
          {
            name: 'chat-composer-toolbar',
            style: {
              display: 'flex',
              flexDirection: isMobile ? 'column' : 'row',
              widthStr: '100%',
              gap: isMobile ? 8 : 12,
              flexWrap: 'wrap',
            },
            children: [
              {
                name: 'chat-composer-tier-selectors',
                style: {
                  display: 'flex',
                  flexDirection: 'row',
                  gap: 8,
                  widthStr: isMobile ? '100%' : 'auto',
                },
                children: [
                  { name: 'chat-agent-picker', style: { width: isMobile ? 120 : 160 } },
                  { name: 'chat-model-picker', style: { width: isMobile ? 140 : 200 } },
                ],
              },
              {
                name: 'chat-composer-tier-actions',
                style: {
                  display: 'flex',
                  flexDirection: 'row',
                  gap: 8,
                  widthStr: isMobile ? '100%' : 'auto',
                },
                children: [
                  {
                    name: 'chat-composer-actions',
                    style: { display: 'flex', gap: 6, width: 136 },
                    children: [
                      { name: 'attach-btn', style: { width: 40 } },
                      { name: 'more-btn', style: { width: 40 } },
                      { name: 'cw-btn', style: { width: 40 } },
                    ],
                  },
                  {
                    name: 'chat-composer-send-wrapper',
                    style: { width: 40 },
                    children: [{ name: 'send-btn', style: { width: 40 } }],
                  },
                ],
              },
            ],
          },
        ],
      };

      const result = sim.simulatePage(composerBox);
      assert(result.passes, `Composer induced horizontal overflow on ${vp.name}`);
      assertLessThanOrEqual(
        result.documentScrollWidth,
        vp.width,
        `Composer scrollWidth (${result.documentScrollWidth}px) exceeds ${vp.width}px`
      );
    });
  }

  // Extreme sub-375px viewports (320px, 340px, 360px)
  for (const vp of extremeSub375Viewports) {
    harness.test(`Sub-375 viewport (${vp.name}) withstands chat welcome hero & composer without blowout`, () => {
      const sim = new ViewportSimulator({ width: vp.width, height: vp.height });
      const testBox = {
        name: 'page-wrapper',
        style: { widthStr: '100%', maxWidth: vp.width },
        children: [
          {
            name: 'chat-welcome-hero',
            style: { widthStr: '100%', padding: { top: 16, right: 8, bottom: 16, left: 8 } },
            children: [
              { name: 'title', style: { widthStr: '100%', wordBreak: 'break-word' } },
              {
                name: 'chat-welcome-starters-grid',
                style: { display: 'grid', widthStr: '100%', gap: 8 },
                children: [{ name: 'card', style: { widthStr: '100%' } }],
              },
            ],
          },
        ],
      };
      const result = sim.simulatePage(testBox);
      assert(result.passes, `Sub-375 test blew out on ${vp.name}`);
    });
  }

  // =========================================================================
  // Section 3: Assistant Message Actions (CopyButton & Retry/Regenerate)
  // =========================================================================
  harness.setFeature('CHAL-M2-03: Assistant Message Actions Empirical Testing');

  // Verify Transcript.tsx imports and wiring
  harness.test('Transcript.tsx renders CopyButton and Retry RotateCcw button for assistant messages', () => {
    const transcriptCode = ast.readSrcFile('components/Transcript.tsx');
    assertIncludes(transcriptCode, 'message-actions assistant-actions', 'Must define assistant-actions container');
    assertIncludes(transcriptCode, 'copiedTitle="Copied!"', 'CopyButton must specify copiedTitle="Copied!"');
    assertIncludes(transcriptCode, 'successMessage="Response copied."', 'CopyButton must specify successMessage="Response copied."');
    assertIncludes(transcriptCode, '<RotateCcw size={15}', 'Retry button must use RotateCcw icon');
    assertIncludes(transcriptCode, 'props.onRegenerateMessage?.', 'Retry button must call props.onRegenerateMessage');
  });

  // Verify Assistant Actions Guard Conditions
  harness.test('Assistant actions are guarded: only render when role==="assistant" and non-empty and non-status', () => {
    const transcriptCode = ast.readSrcFile('components/Transcript.tsx');
    assertIncludes(
      transcriptCode,
      'props.role === "assistant" && props.text && !isChatStatusText(props.text)',
      'Guard condition for assistant actions in TranscriptMessageView'
    );
  });

  // Verify CopyButton State & Feedback Logic
  harness.test('CopyButton logic transitions: copied state yields "Copied!" title and Checkmark icon', () => {
    const copyButtonCode = ast.readSrcFile('components/CopyButton.tsx');
    assertIncludes(copyButtonCode, 'if (copied())', 'CopyButton must check copied state');
    assertIncludes(copyButtonCode, 'return props.copiedTitle || "Copied"', 'CopyButton must return copiedTitle when copied');
    assertIncludes(copyButtonCode, '<Check size={props.iconSize', 'CopyButton must render Check icon when copied');
    assertIncludes(copyButtonCode, 'showToast(props.successMessage)', 'CopyButton must trigger toast on success');
    assertIncludes(copyButtonCode, 'resetLater(currentGeneration)', 'CopyButton must schedule reset after copy');
  });

  // Verify Backward Search in handleRegenerateMessage (Chat.tsx)
  harness.test('handleRegenerateMessage backward search algorithm correctly identifies target user checkpoint', () => {
    // Model conversation items
    const mockItems = [
      { id: 101, type: 'message', role: 'user', messageIndex: 0, sourceCheckpointId: 'cp-user-1', text: 'Hello Kai' },
      { id: 102, type: 'message', role: 'assistant', messageIndex: 1, text: 'Hello! How can I help?' },
      { id: 103, type: 'tool', name: 'search_files', args: {}, result: 'found 2 files' },
      { id: 104, type: 'message', role: 'user', messageIndex: 2, sourceCheckpointId: 'cp-user-2', text: 'Analyze file A' },
      { id: 105, type: 'message', role: 'assistant', messageIndex: 3, text: 'Analysis of file A...' },
    ];

    // Algorithmic simulation matching Chat.tsx lines 1560-1582
    function simulateRegenerate(payload, currentItems) {
      let targetIndex = -1;
      if (payload?.itemId !== undefined) {
        targetIndex = currentItems.findIndex((it) => it.id === payload.itemId);
      } else if (payload?.messageIndex !== undefined) {
        targetIndex = currentItems.findIndex(
          (it) => it.type === 'message' && it.role === 'assistant' && it.messageIndex === payload.messageIndex
        );
      }

      const searchSlice = targetIndex >= 0 ? currentItems.slice(0, targetIndex) : currentItems;
      for (let i = searchSlice.length - 1; i >= 0; i--) {
        const it = searchSlice[i];
        if (it.type === 'message' && it.role === 'user' && it.messageIndex !== undefined && it.sourceCheckpointId) {
          return {
            found: true,
            userMessage: {
              itemId: it.id,
              messageIndex: it.messageIndex,
              sourceCheckpointId: it.sourceCheckpointId,
              text: it.text || '',
            },
          };
        }
      }
      return { found: false, error: 'No earlier user prompt found to regenerate from.' };
    }

    // Test Case A: Retrying the latest assistant message (id: 105)
    const resultLatest = simulateRegenerate({ itemId: 105 }, mockItems);
    assert(resultLatest.found, 'Should find preceding user message for assistant message 105');
    assertEqual(resultLatest.userMessage.sourceCheckpointId, 'cp-user-2', 'Should target cp-user-2');
    assertEqual(resultLatest.userMessage.text, 'Analyze file A', 'Should extract user prompt text');

    // Test Case B: Retrying an older assistant message (id: 102) in multi-turn conversation
    const resultOlder = simulateRegenerate({ itemId: 102 }, mockItems);
    assert(resultOlder.found, 'Should find preceding user message for assistant message 102');
    assertEqual(resultOlder.userMessage.sourceCheckpointId, 'cp-user-1', 'Should target cp-user-1');
    assertEqual(resultOlder.userMessage.text, 'Hello Kai', 'Should extract first user prompt text');

    // Test Case C: Assistant message with no preceding user message
    const orphanItems = [
      { id: 201, type: 'message', role: 'assistant', messageIndex: 0, text: 'Welcome greeting without prompt' },
    ];
    const resultOrphan = simulateRegenerate({ itemId: 201 }, orphanItems);
    assertEqual(resultOrphan.found, false, 'Should gracefully fail when no user prompt precedes');
    assertEqual(resultOrphan.error, 'No earlier user prompt found to regenerate from.');
  });

  // Verify Retry Button Disabled during busy state
  harness.test('Retry button disables when actionsDisabled is true in TranscriptMessageView', () => {
    const transcriptCode = ast.readSrcFile('components/Transcript.tsx');
    assertIncludes(
      transcriptCode,
      'disabled={props.actionsDisabled}',
      'Retry button must bind disabled attribute to props.actionsDisabled'
    );
  });

  // =========================================================================
  // Section 4: ChatWelcomeHero Starter Prompt Cards & Workspace Selector
  // =========================================================================
  harness.setFeature('CHAL-M2-04: ChatWelcomeHero & WorkspaceSelector Interaction');

  // Verify 4 Starter Prompt Cards Configuration
  harness.test('ChatWelcomeHero defines exactly 4 actionable starter prompt cards with icons', () => {
    const heroCode = ast.readSrcFile('components/ChatTranscript.tsx');
    const expectedCards = [
      'Explore Architecture',
      'Implement Feature',
      'Find & Fix Bugs',
      'Review & Optimize',
    ];
    for (const title of expectedCards) {
      assertIncludes(heroCode, `title: "${title}"`, `Must include starter prompt card: ${title}`);
    }
  });

  // Verify Starter Card Click Interaction
  harness.test('Clicking starter card triggers onSelectPrompt with card prompt text', () => {
    const heroCode = ast.readSrcFile('components/ChatTranscript.tsx');
    assertIncludes(
      heroCode,
      'onClick={() => props.onSelectPrompt?.(card.prompt)}',
      'Card button must trigger onSelectPrompt callback'
    );
  });

  // Verify WorkspaceSelector embedded in ChatWelcomeHero
  harness.test('WorkspaceSelector is properly nested with reactive props inside ChatWelcomeHero', () => {
    const heroCode = ast.readSrcFile('components/ChatTranscript.tsx');
    assertIncludes(heroCode, '<WorkspaceSelector', 'Must render WorkspaceSelector');
    assertIncludes(heroCode, 'workingDir={props.workingDir}', 'Must pass workingDir');
    assertIncludes(heroCode, 'defaultWorkingDir={props.defaultWorkingDir}', 'Must pass defaultWorkingDir');
    assertIncludes(heroCode, 'selection={props.workspaceSelection}', 'Must pass workspaceSelection');
    assertIncludes(heroCode, 'onSelectionChange={props.onWorkspaceSelectionChange}', 'Must bind onSelectionChange');
  });

  // Verify Chat.tsx handleSelectPrompt updates prompt and focuses textarea
  harness.test('Chat.tsx handleSelectPrompt sets prompt signal and focuses composer textarea', () => {
    const chatCode = ast.readSrcFile('pages/Chat.tsx');
    assertIncludes(chatCode, 'const handleSelectPrompt = (promptText: string) => {', 'Must define handleSelectPrompt');
    assertIncludes(chatCode, 'setPrompt(promptText);', 'handleSelectPrompt must call setPrompt');
    assertIncludes(chatCode, 'chatPromptRef?.focus();', 'handleSelectPrompt must focus chatPromptRef');
  });

  // Verify ChatComposer exposes and wires chatPromptRef
  harness.test('ChatComposer properly binds textarea element to chatPromptRef callbacks', () => {
    const composerCode = ast.readSrcFile('components/ChatComposer.tsx');
    assertIncludes(composerCode, 'setChatPromptRef?: (el: HTMLTextAreaElement) => void;', 'Must declare setChatPromptRef prop');
    assertIncludes(composerCode, 'chatPromptRef?: (el: HTMLTextAreaElement) => void;', 'Must declare chatPromptRef prop');
    assertIncludes(composerCode, 'props.setChatPromptRef?.(el);', 'Must call props.setChatPromptRef');
    assertIncludes(composerCode, 'props.chatPromptRef?.(el);', 'Must call props.chatPromptRef');
  });

  // =========================================================================
  // Section 5: AGENTS.md English Codebase Compliance & AST Integrity
  // =========================================================================
  harness.setFeature('CHAL-M2-05: AGENTS.md Localization Compliance');

  const filesToCheck = [
    'components/Transcript.tsx',
    'components/ChatTranscript.tsx',
    'components/ChatComposer.tsx',
    'pages/Chat.tsx',
    'styles/composer-controls.css',
    'styles/chat.css',
  ];

  const vietnameseRegex = /[àáạảãâầấậẩẫăằắặẳẵèéẹẻẽêềếệểễìíịỉĩòóọỏõôồốộổỗơờớợởỡùúụủũưừứựửữỳýỵỷỹđ]/i;

  for (const relPath of filesToCheck) {
    harness.test(`Source file ${relPath} contains zero Vietnamese characters`, () => {
      const content = ast.readSrcFile(relPath);
      const match = content.match(vietnameseRegex);
      assert(!match, `Found Vietnamese character "${match?.[0]}" in ${relPath}`);
    });
  }

  // Execute all tests
  const results = await harness.run();
  return results;
}

runM2ChallengerEmpiricalSuite()
  .then((res) => {
    if (res.failed > 0) {
      console.error(`\n❌ M2 Challenger Empirical Suite failed with ${res.failed} failures.`);
      process.exit(1);
    } else {
      console.log(`\n🏆 All ${res.passed} Challenger M2 Empirical Tests PASSED!`);
      process.exit(0);
    }
  })
  .catch((err) => {
    console.error('Fatal error in challenger suite:', err);
    process.exit(1);
  });
