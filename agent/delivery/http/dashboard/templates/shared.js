const UNKNOWN_ERROR = 'Unknown error';
const TOAST_HIDE_DELAY_MS = 3000;
const DEFAULT_RELOAD_DELAY_MS = 600;

function showToast(message, type = 'success') {
    const toast = document.getElementById('toast');
    if (!toast) {
        return;
    }

    toast.textContent = message;
    toast.className = 'toast show';

    if (type === 'error') {
        toast.classList.add('error');
    } else if (type === 'warning') {
        toast.classList.add('warning');
    }

    setTimeout(() => {
        toast.className = 'toast';
    }, TOAST_HIDE_DELAY_MS);
}

async function readError(response) {
    const text = await response.text();
    if (!text) {
        return UNKNOWN_ERROR;
    }

    try {
        const data = JSON.parse(text);
        return data.detail || data.message || UNKNOWN_ERROR;
    } catch {
        return text;
    }
}

function scheduleReload(delay = DEFAULT_RELOAD_DELAY_MS) {
    setTimeout(() => location.reload(), delay);
}

function pad(value) {
    return String(value).padStart(2, '0');
}

function formatDateTimeLocal(date) {
    const year = date.getFullYear();
    const month = pad(date.getMonth() + 1);
    const day = pad(date.getDate());
    const hours = pad(date.getHours());
    const minutes = pad(date.getMinutes());
    return `${year}-${month}-${day}T${hours}:${minutes}`;
}

function closeAllModals() {
    document.querySelectorAll('.modal-overlay.active').forEach((modal) => {
        modal.classList.remove('active');
    });
}

function initModalOverlayClose() {
    document.querySelectorAll('.modal-overlay').forEach((overlay) => {
        overlay.addEventListener('click', (event) => {
            if (event.target !== overlay) {
                return;
            }
            overlay.classList.remove('active');
        });
    });
}

document.addEventListener('DOMContentLoaded', () => {
    initModalOverlayClose();
});
