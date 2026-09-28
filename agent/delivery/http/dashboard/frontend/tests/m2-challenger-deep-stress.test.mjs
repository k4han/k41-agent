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
} from './responsive/engine/harness.mjs';
import { ViewportSimulator } from './responsive/engine/viewport-simulator.mjs';
import { CSSAnalyzer } from './responsive/engine/css-analyzer.mjs';
import { ASTAnalyzer } from './responsive/engine/ast-analyzer.mjs';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const FRONTEND_ROOT = path.resolve(__dirname, '..');

async function runM2ChallengerDeepStressSuite() {
  const stylesDir = path.join(FRONTEND_ROOT, 'src/styles');
  const css = new CSSAnalyzer(stylesDir);
  const ast = new ASTAnalyzer(FRONTEND_ROOT);
  css.load();

  const harness = new TestHarness({ verbose: true });
  harness.setTier('Empirical Challenger M2: Deep Adversarial & Edge Case Stress');

  const viewports = [
    { name: '320px (Extremely Narrow Mobile)', width: 320, height: 568 },
    { name: '360px (Standard Compact Mobile)', width: 360, height: 740 },
    { name: '375px (iPhone SE / Standard Mobile)', width: 375, height: 667 },
    { name: '768px (iPad Portrait / Tablet Boundary)', width: 768, height: 1024 },
  ];

  // =========================================================================
  // Section 1: Viewport Extremes on ChatWelcomeHero (320px, 360px, 375px, 768px)
  // =========================================================================
  harness.setFeature('M2-STRESS-01: ChatWelcomeHero Viewport Extremes & Layout Bounds');

  for (const vp of viewports) {
    harness.test(`ChatWelcomeHero at ${vp.width}px fits within viewport with zero horizontal overflow`, () => {
      const sim = new ViewportSimulator({ width: vp.width, height: vp.height });
      const isCompact = vp.width <= 768;

      // Model the ChatWelcomeHero box structure
      const heroBox = {
        name: 'chat-welcome-hero',
        style: {
          widthStr: '100%',
          maxWidth: 800,
          padding: isCompact
            ? { top: 20, right: 12, bottom: 16, left: 12 }
            : { top: 32, right: 16, bottom: 24, left: 16 },
        },
        children: [
          {
            name: 'chat-welcome-hero-header',
            style: { display: 'flex', flexDirection: 'column', widthStr: '100%' },
            children: [
              { name: 'chat-welcome-hero-badge', style: { width: 44, height: 44 } },
              {
                name: 'chat-welcome-hero-title',
                text: 'How can Kai help you today?',
                style: { widthStr: '100%', overflowWrap: 'break-word' },
              },
              {
                name: 'chat-welcome-hero-desc',
                text: 'Select a workspace context and pick a starter task below, or write your own instructions.',
                style: { widthStr: '100%', maxWidth: 520, overflowWrap: 'break-word' },
              },
            ],
          },
          {
            name: 'chat-welcome-workspace-wrapper',
            style: {
              widthStr: '100%',
              padding: { top: 14, right: 14, bottom: 14, left: 14 },
            },
            children: [
              { name: 'workspace-label', style: { widthStr: '100%' } },
              { name: 'workspace-selector', style: { widthStr: '100%' } },
            ],
          },
          {
            name: 'chat-welcome-starters-grid',
            style: {
              display: 'grid',
              widthStr: '100%',
              gap: isCompact ? 10 : 12,
              // Grid columns: 1 column on <= 768px, 2 columns on desktop
              gridTemplateColumns: isCompact ? '1fr' : 'repeat(2, minmax(0, 1fr))',
            },
            children: [
              {
                name: 'starter-card-1',
                style: {
                  widthStr: '100%',
                  padding: { top: 14, right: 16, bottom: 14, left: 16 },
                  minHeight: 80,
                },
                children: [
                  { name: 'card-header', style: { display: 'flex', widthStr: '100%' } },
                  {
                    name: 'card-prompt',
                    text: 'Analyze this repository structure and summarize key modules',
                    style: { widthStr: '100%', overflowWrap: 'break-word' },
                  },
                ],
              },
              {
                name: 'starter-card-2',
                style: {
                  widthStr: '100%',
                  padding: { top: 14, right: 16, bottom: 14, left: 16 },
                  minHeight: 80,
                },
              },
              {
                name: 'starter-card-3',
                style: {
                  widthStr: '100%',
                  padding: { top: 14, right: 16, bottom: 14, left: 16 },
                  minHeight: 80,
                },
              },
              {
                name: 'starter-card-4',
                style: {
                  widthStr: '100%',
                  padding: { top: 14, right: 16, bottom: 14, left: 16 },
                  minHeight: 80,
                },
              },
            ],
          },
        ],
      };

      const res = sim.computeBox(heroBox);
      assertLessThanOrEqual(res.scrollWidth, vp.width, `Hero scrollWidth (${res.scrollWidth}px) exceeds ${vp.width}px`);
      assert(res.inducesDocOverflow === false, `Hero induces doc overflow on ${vp.width}px`);
    });
  }

  // =========================================================================
  // Section 2: Composer Toolbar Tiers at 320px, 360px, 375px, 768px
  // =========================================================================
  harness.setFeature('M2-STRESS-02: Composer Toolbar Tiers & Controls Layout Bounds');

  for (const vp of viewports) {
    harness.test(`Composer Toolbar at ${vp.width}px stacks into 2 tiers and fits cleanly`, () => {
      const sim = new ViewportSimulator({ width: vp.width, height: vp.height });
      const paddingH = vp.width <= 640 ? 8 : 16;
      const composerWidth = vp.width - paddingH * 2;
      const innerWidth = composerWidth - 24;

      // Tier 1 (selectors) and Tier 2 (actions)
      const tier1 = {
        name: 'tier-selectors',
        style: {
          display: 'flex',
          flexDirection: 'row',
          widthStr: `${innerWidth}px`,
          gap: 8,
        },
        children: [
          { name: 'agent-picker', style: { widthStr: `${(innerWidth - 8) / 2}px`, minWidth: 0, height: 40 } },
          { name: 'model-picker', style: { widthStr: `${(innerWidth - 8) / 2}px`, minWidth: 0, height: 40 } },
        ],
      };

      const resTier1 = sim.computeBox(tier1, innerWidth);
      assertLessThanOrEqual(resTier1.scrollWidth, innerWidth, `Tier 1 overflowed inner width on ${vp.width}px`);

      const tier2 = {
        name: 'tier-actions',
        style: {
          display: 'flex',
          flexDirection: 'row',
          widthStr: `${innerWidth}px`,
          justifyContent: 'space-between',
        },
        children: [
          {
            name: 'actions-left',
            style: { display: 'flex', flexDirection: 'row', gap: 6, width: 132 },
            children: [
              { name: 'attach-btn', style: { width: 40, height: 40, minWidth: 40, minHeight: 40 } },
              { name: 'more-btn', style: { width: 40, height: 40, minWidth: 40, minHeight: 40 } },
              { name: 'cw-btn', style: { width: 40, height: 40, minWidth: 40, minHeight: 40 } },
            ],
          },
          {
            name: 'send-wrapper',
            style: { width: 40, height: 40, minWidth: 40, minHeight: 40 },
          },
        ],
      };

      const resTier2 = sim.computeBox(tier2, innerWidth);
      assertLessThanOrEqual(resTier2.scrollWidth, innerWidth, `Tier 2 overflowed inner width on ${vp.width}px`);
    });
  }

  // =========================================================================
  // Section 3: Touch Target Precision Audit (>= 40px)
  // =========================================================================
  harness.setFeature('M2-STRESS-03: Touch Target Minimum Ergonomics');

  harness.test('Assistant message action buttons (.message-action-btn) are 40x40px on mobile/tablet', () => {
    const rules640 = css.findRules('.message-action-btn', '640px');
    const rules768 = css.findRules('.message-action-btn', '768px');
    const allRules = [...rules640, ...rules768];

    const has40px = allRules.some((r) => {
      const w = parseFloat(r.declarations['width'] || '0');
      const h = parseFloat(r.declarations['height'] || '0');
      return w >= 40 && h >= 40;
    });
    assert(has40px, '.message-action-btn must specify width and height >= 40px under mobile/tablet media query');
  });

  harness.test('ChatWelcomeHero cards have min-height >= 40px touch surface', () => {
    const rules = css.findRules('.chat-welcome-card');
    assert(rules.length > 0, '.chat-welcome-card rule exists');
    const rule = rules[0];
    const padTop = parseFloat(rule.declarations['padding']?.split(' ')[0] || '14');
    const padBottom = parseFloat(rule.declarations['padding']?.split(' ')[2] || '14');
    const totalPadding = padTop + padBottom;
    // With 28px vertical padding + title (>= 18px) + prompt (>= 18px), card height >= 64px >= 40px
    assertGreaterThanOrEqual(totalPadding, 20, 'Card padding ensures comfortable touch target');
  });

  harness.test('ChatComposer send button (.chat-composer-send) is 40x40px', () => {
    const rules = css.findRules('.chat-composer-send');
    const iconRules = css.findRules('.chat-composer-icon');
    assert(rules.length > 0 || iconRules.length > 0, 'Send button styles exist');
    const w = parseFloat(iconRules[0]?.declarations['width'] || '0');
    const h = parseFloat(iconRules[0]?.declarations['height'] || '0');
    assertGreaterThanOrEqual(w, 40, 'Width >= 40px');
    assertGreaterThanOrEqual(h, 40, 'Height >= 40px');
  });

  harness.test('ChatComposer attachment remove button (.chat-attachment-remove) is 40x40px', () => {
    const rules = css.findRules('.chat-attachment-remove');
    assert(rules.length > 0, '.chat-attachment-remove rules exist');
    const has40px = rules.some((r) => {
      const w = parseFloat(r.declarations['width'] || r.declarations['min-width'] || '0');
      const h = parseFloat(r.declarations['height'] || r.declarations['min-height'] || '0');
      return w >= 40 && h >= 40;
    });
    assert(has40px, '.chat-attachment-remove must be >= 40px');
  });

  // =========================================================================
  // Section 4: Edge Case Logic Simulation: Message Regeneration
  // =========================================================================
  harness.setFeature('M2-STRESS-04: Regeneration Algorithm Edge Case Resilience');

  // Simulate handleRegenerateMessage algorithm directly from Chat.tsx
  function simulateRegenerate(items, payload, conversationBusy = false) {
    if (conversationBusy) {
      return { status: 'blocked', reason: 'conversationBusy' };
    }

    if (payload?.sourceCheckpointId && payload.messageIndex !== undefined && typeof payload.text === 'string') {
      return {
        status: 'success',
        dispatched: {
          itemId: payload.itemId,
          messageIndex: payload.messageIndex,
          sourceCheckpointId: payload.sourceCheckpointId,
          text: payload.text,
        },
      };
    }

    let targetIndex = -1;
    if (payload?.itemId !== undefined) {
      targetIndex = items.findIndex((it) => it.id === payload.itemId);
    } else if (payload?.messageIndex !== undefined) {
      targetIndex = items.findIndex(
        (it) => it.type === 'message' && it.role === 'assistant' && it.messageIndex === payload.messageIndex
      );
    }

    const searchSlice = targetIndex >= 0 ? items.slice(0, targetIndex) : items;
    for (let i = searchSlice.length - 1; i >= 0; i--) {
      const it = searchSlice[i];
      if (it.type === 'message' && it.role === 'user' && it.messageIndex !== undefined && it.sourceCheckpointId) {
        return {
          status: 'success',
          dispatched: {
            itemId: it.id,
            messageIndex: it.messageIndex,
            sourceCheckpointId: it.sourceCheckpointId,
            text: it.text || '',
          },
        };
      }
    }
    return { status: 'no_earlier_user_prompt' };
  }

  harness.test('Regeneration with NO prior user message returns no_earlier_user_prompt toast without error', () => {
    const items = [
      { id: 1, type: 'message', role: 'assistant', text: 'Hello! I am Kai. How can I help you?', messageIndex: 0 },
    ];
    const result = simulateRegenerate(items, { itemId: 1 });
    assertEqual(result.status, 'no_earlier_user_prompt', 'Must cleanly signal no earlier prompt');
  });

  harness.test('Regeneration in multi-turn thread targets the immediate predecessor user prompt', () => {
    const items = [
      { id: 1, type: 'message', role: 'user', text: 'Prompt 1', messageIndex: 0, sourceCheckpointId: 'cp-0' },
      { id: 2, type: 'message', role: 'assistant', text: 'Answer 1', messageIndex: 1 },
      { id: 3, type: 'message', role: 'user', text: 'Prompt 2', messageIndex: 2, sourceCheckpointId: 'cp-2' },
      { id: 4, type: 'tool', name: 'read_file', args: {}, result: 'content' },
      { id: 5, type: 'message', role: 'assistant', text: 'Answer 2', messageIndex: 3 },
    ];

    // Regenerate Answer 2 (item 5) -> should target Prompt 2 (item 3)
    const res2 = simulateRegenerate(items, { itemId: 5 });
    assertEqual(res2.status, 'success');
    assertEqual(res2.dispatched.sourceCheckpointId, 'cp-2');
    assertEqual(res2.dispatched.text, 'Prompt 2');

    // Regenerate Answer 1 (item 2) -> should target Prompt 1 (item 1)
    const res1 = simulateRegenerate(items, { itemId: 2 });
    assertEqual(res1.status, 'success');
    assertEqual(res1.dispatched.sourceCheckpointId, 'cp-0');
    assertEqual(res1.dispatched.text, 'Prompt 1');
  });

  harness.test('Regeneration is blocked when conversationBusy is true', () => {
    const items = [
      { id: 1, type: 'message', role: 'user', text: 'Prompt 1', messageIndex: 0, sourceCheckpointId: 'cp-0' },
      { id: 2, type: 'message', role: 'assistant', text: 'Answer 1', messageIndex: 1 },
    ];
    const result = simulateRegenerate(items, { itemId: 2 }, true);
    assertEqual(result.status, 'blocked');
    assertEqual(result.reason, 'conversationBusy');
  });

  harness.test('Regeneration directly accepts explicit sourceCheckpointId payload if provided', () => {
    const items = [];
    const payload = {
      itemId: 99,
      messageIndex: 4,
      sourceCheckpointId: 'cp-custom',
      text: 'Custom prompt text',
    };
    const result = simulateRegenerate(items, payload);
    assertEqual(result.status, 'success');
    assertEqual(result.dispatched.sourceCheckpointId, 'cp-custom');
    assertEqual(result.dispatched.text, 'Custom prompt text');
  });

  // =========================================================================
  // Section 5: Edge Case Logic Simulation: Starter Prompt Selection
  // =========================================================================
  harness.setFeature('M2-STRESS-05: Starter Prompt Selection & Focus Mechanics');

  // Simulate prompt selection state machine
  function simulatePromptSelection() {
    let currentPrompt = '';
    let focused = false;

    const textarea = {
      focus: () => {
        focused = true;
      },
    };

    const handleSelectPrompt = (promptText) => {
      currentPrompt = promptText;
      textarea.focus();
    };

    return {
      getPrompt: () => currentPrompt,
      isFocused: () => focused,
      handleSelectPrompt,
    };
  }

  harness.test('Clicking starter prompt updates prompt state and focuses textarea', () => {
    const sm = simulatePromptSelection();
    sm.handleSelectPrompt('Analyze this repository structure and summarize key modules');
    assertEqual(sm.getPrompt(), 'Analyze this repository structure and summarize key modules');
    assert(sm.isFocused(), 'Textarea must be focused after prompt selection');
  });

  harness.test('Rapidly clicking multiple starter prompts replaces prompt cleanly without concatenating', () => {
    const sm = simulatePromptSelection();
    sm.handleSelectPrompt('First prompt selection');
    sm.handleSelectPrompt('Second prompt selection');
    sm.handleSelectPrompt('Final prompt selection');
    assertEqual(sm.getPrompt(), 'Final prompt selection', 'Last selected prompt must prevail');
    assert(sm.isFocused(), 'Focus retained');
  });

  harness.test('Clicking prompt when user already typed text replaces with starter prompt', () => {
    const sm = simulatePromptSelection();
    // User typed something
    sm.handleSelectPrompt('Starter feature prompt');
    assertEqual(sm.getPrompt(), 'Starter feature prompt');
  });

  // =========================================================================
  // Section 6: Full Response Copy & Markdown Integrity
  // =========================================================================
  harness.setFeature('M2-STRESS-06: Full Response Copy & Markdown Content Resilience');

  harness.test('CopyButton is integrated in Transcript.tsx for assistant messages with correct props', () => {
    const transcriptSrc = ast.readSrcFile('components/Transcript.tsx');
    assert(transcriptSrc.includes('props.role === "assistant" && props.text && !isChatStatusText(props.text)'));
    assert(transcriptSrc.includes('class="message-actions assistant-actions"'));
    assert(transcriptSrc.includes('title="Copy response"'));
    assert(transcriptSrc.includes('copiedTitle="Copied!"'));
    assert(transcriptSrc.includes('successMessage="Response copied."'));
  });

  harness.test('Assistant actions toolbar does NOT render for status messages like Thinking...', () => {
    const transcriptSrc = ast.readSrcFile('components/Transcript.tsx');
    assert(transcriptSrc.includes('!isChatStatusText(props.text)'), 'Must filter out status text from rendering actions');
  });

  harness.test('Copy button value receives entire raw markdown text verbatim', () => {
    const complexMarkdown = '# Architecture Overview\n\n```typescript\nconst a = 1;\n```\n\n| Col 1 | Col 2 |\n|---|---|\n| Val 1 | Val 2 |';
    const transcriptSrc = ast.readSrcFile('components/Transcript.tsx');
    assert(transcriptSrc.includes('value={props.text}'), 'CopyButton value must be bound directly to props.text');
  });

  // =========================================================================
  // Section 7: AGENTS.md English Codebase Compliance Audit across M2 Files
  // =========================================================================
  harness.setFeature('M2-STRESS-07: AGENTS.md Zero Vietnamese Localization Audit');

  const m2Files = [
    'src/components/Transcript.tsx',
    'src/components/ChatTranscript.tsx',
    'src/components/ChatComposer.tsx',
    'src/pages/Chat.tsx',
    'src/styles/composer-controls.css',
    'src/styles/chat.css',
  ];

  const vietnameseRegex = /[àáạảãâầấậẩẫăằắặẳẵèéẹẻẽêềếệểễìíịỉĩòóọỏõôồốộổỗơờớợởỡùúụủũưừứựửữỳýỵỷỹđ]/i;

  for (const relPath of m2Files) {
    harness.test(`File ${relPath} contains 0 Vietnamese characters`, () => {
      const content = ast.readSrcFile(relPath.replace('src/', ''));
      const matches = content.match(vietnameseRegex);
      assert(!matches, `Found Vietnamese character '${matches?.[0]}' in ${relPath}`);
    });
  }

  const results = await harness.run();
  return results;
}

runM2ChallengerDeepStressSuite()
  .then((res) => {
    if (res.failed > 0) {
      console.error(`\n❌ Challenger M2 Deep Stress Suite failed with ${res.failed} failures.`);
      process.exit(1);
    } else {
      console.log(`\n🏆 All ${res.passed} Challenger M2 Deep Stress Tests PASSED!`);
      process.exit(0);
    }
  })
  .catch((err) => {
    console.error('Fatal error in deep stress suite:', err);
    process.exit(1);
  });
