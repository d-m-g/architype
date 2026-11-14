export const demoSteps = [
  {title:'Count with evidence', scenario:'windows', view:'2d', cue:'Six windows, six numbered highlights. Select a highlight to show its source reference.', say:'Every answer should let a person check the drawing. This synthetic example links each counted window to evidence.'},
  {title:'Show the calculation', scenario:'area', view:'2d', cue:'Select each room and point to 32.4 + 18.6 = 51.0 m².', say:'The answer shows which rooms contributed and how their supplied quantities were added. These are mock quantities, not measurements from this image.'},
  {title:'Make uncertainty visible', scenario:'doors', view:'2d', cue:'Select the amber bathroom-door candidate.', say:'Uncertain candidates remain visible for review. Three candidates does not mean three verified doors.'},
  {title:'Know when to abstain', scenario:'missing', view:'2d', cue:'Show the whole-building question and the missing answer.', say:'One floor cannot establish a whole-building total. Missing evidence stays missing rather than becoming a confident zero.'},
  {title:'Explore the same evidence in 3D', scenario:'area', view:'3d', cue:'Orbit the model and select a room. If WebGL is unavailable, use 2D and continue.', say:'The same source polygons drive this illustrative 3D view. Heights are not measured and this is not a reconstructed BIM model.'},
  {title:'Ask and listen', scenario:'windows', view:'2d', cue:'Click Read answer. Dictation is optional: review the transcript before submitting. The mock accepts the exact English sample question only.', say:'Voice changes the input and output, not the evidence pipeline. English and Finnish dictation still need a live microphone check; answer narration is English.'},
  {title:'Explain the evaluation', panel:'dashboard', cue:'Move the threshold slider and inspect the failure table. All eight cases and costs shown here are synthetic.', say:'The dashboard demonstrates coverage, confidence and cost tradeoffs. Real evaluation logs can be imported separately; we do not present mock metrics as measured performance.'}
];
export function mountDemo(root = document) {
  const get = id => root.getElementById(id);
  let index = -1, applying = false;
  const stopVoice = () => {
    if (!get('voice-stop').disabled) get('voice-stop').click();
    if (get('voice-dictate').getAttribute('aria-pressed') === 'true') get('voice-dictate').click();
  };
  const end = () => { stopVoice(); index = -1; get('demo-guide').hidden = true; get('demo-start').hidden = false; };
  const show = next => {
    index = Math.max(0, Math.min(demoSteps.length - 1, next));
    const step = demoSteps[index];
    applying = true;
    try {
      stopVoice();
      root.querySelector('[data-panel="' + (step.panel || 'editor') + '"]').click();
      if (step.scenario) {
        root.querySelector('[data-scenario="' + step.scenario + '"]').click();
        get('view-' + step.view).click();
        get('overlay-toggle').checked = true;
        get('overlay-toggle').dispatchEvent(new Event('change', {bubbles:true}));
        get('setting-labels').checked = true;
        get('setting-labels').dispatchEvent(new Event('change', {bubbles:true}));
      }
      if (step.panel === 'dashboard') { get('show-mock').click(); get('reset').click(); }
      get('demo-guide').hidden = false;
      get('demo-start').hidden = true;
      get('demo-progress').textContent = 'MOCK DEMO · ' + (index + 1) + ' / ' + demoSteps.length;
      get('demo-title').textContent = step.title;
      get('demo-cue').textContent = step.cue;
      get('demo-script').textContent = step.say;
      get('demo-back').disabled = index === 0;
      get('demo-next').textContent = index === demoSteps.length - 1 ? 'Finish demo' : 'Next step';
      get('demo-title').focus({preventScroll:true});
      get('demo-guide').scrollIntoView({block:'start'});
    } finally { applying = false; }
  };
  window.addEventListener('stoa:restore-answer',()=>{if(index>=0)end();});
  get('demo-start').onclick = () => show(0);
  get('demo-back').onclick = () => show(index - 1);
  get('demo-next').onclick = () => { if (index === demoSteps.length - 1) { end(); get('demo-start').focus(); } else show(index + 1); };
  get('demo-exit').onclick = () => { end(); get('demo-start').focus(); };
  root.addEventListener('click', event => {
    if (!applying && index >= 0 && event.target.closest('[data-panel],[data-scenario],#load-json,#ask-api,#show-mock,#use-live-drawing')) end();
  }, true);
  root.addEventListener('change', event => { if (!applying && index >= 0 && ['run-file','drawing-file'].includes(event.target.id)) end(); });
}
if (typeof document !== 'undefined') mountDemo();
