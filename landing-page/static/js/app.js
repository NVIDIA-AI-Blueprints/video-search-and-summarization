// Smooth scrolling for anchor links
document.querySelectorAll('a[href^="#"]').forEach(anchor => {
    anchor.addEventListener('click', function (e) {
        e.preventDefault();
        const target = document.querySelector(this.getAttribute('href'));
        if (target) {
            target.scrollIntoView({
                behavior: 'smooth',
                block: 'start'
            });
        }
    });
});

// Waitlist form submission
const waitlistForm = document.getElementById('waitlist-form');
const successMessage = document.getElementById('success-message');
const errorMessage = document.getElementById('error-message');

waitlistForm.addEventListener('submit', async (e) => {
    e.preventDefault();

    // Get form data
    const formData = {
        name: document.getElementById('name').value,
        email: document.getElementById('email').value,
        company: document.getElementById('company').value,
        industry: document.getElementById('industry').value,
        timestamp: new Date().toISOString()
    };

    // Disable submit button
    const submitButton = waitlistForm.querySelector('button[type="submit"]');
    submitButton.disabled = true;
    submitButton.textContent = 'Joining...';

    try {
        const response = await fetch('/api/waitlist', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify(formData)
        });

        if (response.ok) {
            // Show success message
            successMessage.classList.add('show');
            errorMessage.classList.remove('show');

            // Reset form
            waitlistForm.reset();

            // Track conversion (if analytics is set up)
            if (typeof gtag !== 'undefined') {
                gtag('event', 'waitlist_signup', {
                    'event_category': 'engagement',
                    'event_label': 'Waitlist Form'
                });
            }

            // Hide success message after 10 seconds
            setTimeout(() => {
                successMessage.classList.remove('show');
            }, 10000);
        } else {
            const error = await response.json();
            throw new Error(error.detail || 'Failed to join waitlist');
        }
    } catch (error) {
        console.error('Error:', error);
        document.getElementById('error-text').textContent = error.message;
        errorMessage.classList.add('show');
        successMessage.classList.remove('show');

        // Hide error message after 10 seconds
        setTimeout(() => {
            errorMessage.classList.remove('show');
        }, 10000);
    } finally {
        // Re-enable submit button
        submitButton.disabled = false;
        submitButton.textContent = 'Join the Waitlist →';
    }
});

// Add navbar shadow on scroll
window.addEventListener('scroll', () => {
    const nav = document.querySelector('nav');
    if (window.scrollY > 10) {
        nav.classList.add('shadow-lg');
    } else {
        nav.classList.remove('shadow-lg');
    }
});
