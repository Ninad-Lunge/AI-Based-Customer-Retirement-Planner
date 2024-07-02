import React from 'react';
import { BrowserRouter, Route, Routes } from 'react-router-dom';
// import About from './Components/About';

import Home from './Pages/Home';
import Plan from './Pages/Plan';
import ResultPage from './Pages/ResultPage';

import './App.css';

function App() {
  return (
    <>
    <BrowserRouter>
      {/* <Home/> */}
      {/* <Plan/> */}
       <Routes>
        <Route exact path='/' element={<Home />} />
        <Route exact path='/Home' element={<Home />} />
        <Route exact path='/Plan' element={<Plan />} />
        <Route exact path='/result' element={<ResultPage />} />
       </Routes>
    </BrowserRouter>
    </>
  );
}

export default App;
