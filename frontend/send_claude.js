(function() {
    let textarea = document.querySelector('textarea, [contenteditable="true"]');
    if (!textarea) return 'NO_INPUT';

    textarea.focus();

    let event = new KeyboardEvent('keydown', {
        bubbles: true,
        cancelable: true,
        key: 'Enter',
        code: 'Enter'
    });

    textarea.dispatchEvent(event);

    return 'SENT';
})();