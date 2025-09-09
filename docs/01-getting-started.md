# Getting Started

This guide will walk you through setting up your own miniature weather display. The project uses a Raspberry Pi and a small OLED screens to show live weather data from the OpenWeatherMap API.

## Hardware Requirements ⚙️

The hardware for this project remains the same as the original Train Departure Display.

- Raspberry Pi (Zero, Zero 2, 3A+, 3B+, 4)
- 2.42" 256x64 SPI OLED Display Module (SSD1322)
- Micro SD Card (8GB minimum)
- Power Supply for your Raspberry Pi
- Jumper wires (female-female)
- Optional: 3D printed case (files available in the `case/` directory)
- Optional: A second OLED display for dual-screen mode

## Software Requirements 🖥️

1.  **balenaCloud Account**: We'll use balenaCloud to deploy the software to the Raspberry Pi. It makes setup and configuration much easier. [Sign up for a free account here](https://dashboard.balena-cloud.com/register).
2.  **OpenWeatherMap API Key**: The display fetches data from the OpenWeatherMap "One Call" API. You'll need to sign up for a free account and get an API key.
    * Go to [https://openweathermap.org/](https://openweathermap.org/) and create an account.
    * Navigate to the "API keys" tab in your user dashboard.
    * Copy the key. You'll need it during the configuration step. **Note:** It can take up to an hour for a new API key to become active.

Once you have the hardware and have signed up for the necessary accounts, you're ready to move on to the next step.

**Next Step**: [Connecting the display to the Pi &rarr;](/docs/02-connecting-the-display-to-the-pi.md)